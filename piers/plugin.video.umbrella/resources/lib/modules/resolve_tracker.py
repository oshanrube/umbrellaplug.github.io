# -*- coding: utf-8 -*-
"""
	Umbrella Add-on
	Runs source resolve attempts for playItem() and records what each one is doing,
	so the progress dialog can show the current debrid step and why sources failed.
"""

from threading import Thread, local
from time import time

SOFT_TIMEOUT = 10 # seconds a source may resolve alone before the next source is started alongside it
HARD_TIMEOUT = 45 # seconds before a source is given up on (its thread finishes in the background, result ignored)
MAX_PARALLEL = 3 # matches the Real-Debrid magnet semaphore
_local = local()


def report(message):
	"""Called from inside a resolver to describe the step it is on. No-op outside a tracked attempt."""
	attempt = getattr(_local, 'attempt', None)
	if attempt is None or attempt.abandoned: return
	attempt.status = message
	_debug('#%02d %s' % (attempt.index, message))

def fail(reason, overwrite=True):
	"""Called from inside a resolver to record why it is returning no link."""
	attempt = getattr(_local, 'attempt', None)
	if attempt is None or attempt.abandoned: return
	if attempt.reason and not overwrite: return
	attempt.reason = attempt.status = reason
	_debug('#%02d FAILED: %s' % (attempt.index, reason))

def _debug(message):
	try:
		from resources.lib.modules import log_utils
		log_utils.log('Resolve %s' % message, level=log_utils.LOGDEBUG)
	except: pass

def is_playable_url(url, video_extensions):
	if not url: return False
	lower = url.lower()
	if any(x in lower for x in video_extensions): return True
	return any(x in url for x in ('plex.direct:', 'torbox', 'tb-cdn', 'plugin://plugin.video.composite_for_plex'))

def describe(item):
	return item.get('debrid') or '%s - %s' % (item.get('source', ''), item.get('provider', ''))


class ResolveAttempt:
	def __init__(self, index, item):
		self.index = index # 1-based position in the source list
		self.item = item
		self.url = None
		self.status = 'Queued'
		self.reason = ''
		self.started = None
		self.finished = None
		self.abandoned = False

	@property
	def elapsed(self):
		if self.started is None: return 0.0
		return (self.finished or time()) - self.started


class ResolveRunner:
	"""Resolves sources in list order. A source that is still working after SOFT_TIMEOUT keeps running while the
	next one starts, so a slow debrid call no longer blocks the queue and a late success is not thrown away."""
	def __init__(self, entries, resolve_func, is_valid, soft_timeout=SOFT_TIMEOUT, hard_timeout=HARD_TIMEOUT, max_parallel=MAX_PARALLEL, clock=time):
		self.attempts = [ResolveAttempt(index, item) for index, item in entries]
		self.pending = []
		self.winner = None
		self._queue = list(self.attempts)
		self._resolve = resolve_func
		self._is_valid = is_valid
		self._soft_timeout = soft_timeout
		self._hard_timeout = hard_timeout
		self._max_parallel = max_parallel
		self._clock = clock

	@property
	def exhausted(self):
		return not self._queue and not self.pending

	@property
	def current(self):
		return self.pending[-1] if self.pending else None

	@property
	def percent(self):
		done = len([a for a in self.attempts if a.finished is not None])
		return max(1, int(100 * done / float(len(self.attempts) or 1)))

	def poll(self):
		"""Advance the queue. Returns the winning attempt once one has resolved to a playable url."""
		now = self._clock()
		for attempt in list(self.pending): # pending is in list order, so the higher ranked source wins a tie
			if attempt.finished is not None:
				self.pending.remove(attempt)
				if attempt.url and self.winner is None: self.winner = attempt
			elif now - attempt.started >= self._hard_timeout:
				self._abandon(attempt, 'Timed out after %ds' % self._hard_timeout, now)
		if self.winner: return self.winner
		if self._queue and len(self.pending) < self._max_parallel:
			if all(now - a.started >= self._soft_timeout for a in self.pending):
				self._start(self._queue.pop(0), now)
		return None

	def abandon_all(self, reason='Cancelled'):
		now = self._clock()
		for attempt in list(self.pending): self._abandon(attempt, reason, now)
		self._queue = []

	def summary(self):
		lines = []
		for a in self.attempts:
			if a.started is None: continue
			outcome = 'Playing' if a is self.winner else (a.reason or a.status)
			lines.append('#%02d  %s  |  %s  |  %s' % (a.index, describe(a.item).upper(), a.item.get('quality', ''), a.item.get('name', '')))
			lines.append('        -> %s  (%.1fs)' % (outcome, a.elapsed))
		skipped = len([a for a in self.attempts if a.started is None])
		if skipped: lines.append('%d more source(s) not tried' % skipped)
		return '\n'.join(lines)

	def _abandon(self, attempt, reason, now):
		attempt.abandoned = True
		attempt.reason = attempt.status = reason
		attempt.finished = now
		self.pending.remove(attempt)

	def _start(self, attempt, now):
		attempt.started = now
		attempt.status = 'Starting'
		self.pending.append(attempt)
		Thread(target=self._work, args=(attempt,)).start()

	def _work(self, attempt):
		_local.attempt = attempt
		url, error = None, None
		try: url = self._resolve(attempt.item)
		except Exception as e: error = e
		finally: _local.attempt = None
		if attempt.abandoned: return
		if url and not self._is_valid(url):
			attempt.reason = 'Link is not a supported video file: %s' % url.split('|')[0][-80:]
			url = None
		elif not url and not attempt.reason:
			attempt.reason = 'Error: %s' % error if error else 'No playable link returned'
		attempt.status = 'Resolved' if url else attempt.reason
		attempt.url = url
		attempt.finished = self._clock() # set last, poll() treats it as the completion signal
