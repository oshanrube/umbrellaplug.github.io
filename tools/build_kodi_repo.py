#!/usr/bin/env python3
"""
Rebuilds the Kodi repository files for this fork.

For each Kodi version folder (matrix, nexus, omega) it:
  - zips <dir>/plugin.video.umbrella into <dir>/zips/plugin.video.umbrella/plugin.video.umbrella-<version>.zip
  - zips the repository add-on into <dir>/zips/<repository id>/<repository id>-<version>.zip
  - replaces those two entries in <dir>/zips/addons.xml and rewrites addons.xml.md5
Then it copies the repository zip to the site root and rewrites index.html (the page Kodi's file manager browses).
piers (Kodi 22) has no zips folder: the repository's omega entry covers Kodi 20.90 and up.

Zips are built from HEAD with `git archive`, so they contain committed files only, with LF line endings.
Commit add-on changes (and bump the version in addon.xml) before running.

Usage: python tools/build_kodi_repo.py
"""
import hashlib
import io
import os
import re
import subprocess
import sys
import tarfile
import time
import xml.etree.ElementTree as ET
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KODI_DIRS = ('matrix', 'nexus', 'omega')
REPO_ADDON = 'repository.umbrella.oshanrube'
ASSETS = ('addon.xml', 'icon.png', 'fanart.jpg')


def git(*args):
	return subprocess.check_output(('git',) + args, cwd=ROOT)


def addon_info(src):
	root = ET.fromstring(git('show', 'HEAD:%s/addon.xml' % src))
	return root.get('id'), root.get('version')


def addon_entry(src):
	text = git('show', 'HEAD:%s/addon.xml' % src).decode('utf-8').replace('\r\n', '\n')
	return re.sub(r'^\s*<\?xml[^>]*\?>\s*', '', text).strip()


def build_zip(src, addon_id, dest):
	archive = tarfile.open(fileobj=io.BytesIO(git('archive', '--format=tar', 'HEAD', src)))
	with zipfile.ZipFile(dest, 'w', zipfile.ZIP_DEFLATED) as zf:
		for member in archive.getmembers():
			if not member.isfile(): continue
			name = '%s/%s' % (addon_id, member.name[len(src) + 1:])
			info = zipfile.ZipInfo(name, date_time=time.gmtime(member.mtime)[:6]) # commit time, so rebuilds are byte-identical
			info.compress_type = zipfile.ZIP_DEFLATED
			info.external_attr = 0o644 << 16
			zf.writestr(info, archive.extractfile(member).read())


def publish_addon(kodi_dir, src):
	addon_id, version = addon_info(src)
	dest_dir = os.path.join(ROOT, kodi_dir, 'zips', addon_id)
	os.makedirs(dest_dir, exist_ok=True)
	for f in os.listdir(dest_dir):
		if f.startswith(addon_id + '-') and f.endswith('.zip'): os.remove(os.path.join(dest_dir, f))
	zip_path = os.path.join(dest_dir, '%s-%s.zip' % (addon_id, version))
	build_zip(src, addon_id, zip_path)
	for asset in ASSETS: # Kodi shows these when browsing the repository
		with open(os.path.join(dest_dir, asset), 'wb') as f: f.write(git('show', 'HEAD:%s/%s' % (src, asset)))
	return addon_id, version, zip_path


def update_addons_xml(kodi_dir, sources):
	path = os.path.join(ROOT, kodi_dir, 'zips', 'addons.xml')
	with open(path, encoding='utf-8') as f: text = f.read().replace('\r\n', '\n')
	for src in sources:
		addon_id, _ = addon_info(src)
		entry = addon_entry(src)
		pattern = re.compile(r'<addon\b[^>]*\bid="%s"[^>]*>.*?</addon>' % re.escape(addon_id), re.S)
		if pattern.search(text): text = pattern.sub(lambda m: entry, text, count=1) # function replacement: addon.xml contains backslashes
		else: text = text.replace('</addons>', entry + '\n</addons>')
	data = text.encode('utf-8')
	with open(path, 'wb') as f: f.write(data)
	with open(path + '.md5', 'w', newline='\n') as f: f.write(hashlib.md5(data).hexdigest())


def publish_site(repo_zip):
	name = os.path.basename(repo_zip)
	for f in os.listdir(ROOT):
		if f.startswith(REPO_ADDON + '-') and f.endswith('.zip'): os.remove(os.path.join(ROOT, f))
	with open(repo_zip, 'rb') as src, open(os.path.join(ROOT, name), 'wb') as dest: dest.write(src.read())
	with open(os.path.join(ROOT, 'index.html'), 'w', newline='\n') as f:
		f.write('<!DOCTYPE html>\n<a href="%s">%s</a>\n' % (name, name))


def main():
	sources = [REPO_ADDON] + ['%s/plugin.video.umbrella' % d for d in KODI_DIRS]
	dirty = git('status', '--porcelain', '--', *sources).decode().strip()
	if dirty:
		sys.exit('Uncommitted changes in add-on sources (zips are built from HEAD), commit them first:\n%s' % dirty)
	repo_zip = None
	for kodi_dir in KODI_DIRS:
		plugin_src = '%s/plugin.video.umbrella' % kodi_dir
		for src in (plugin_src, REPO_ADDON):
			addon_id, version, zip_path = publish_addon(kodi_dir, src)
			print('%-7s %s %s' % (kodi_dir, addon_id, version))
			if src == REPO_ADDON: repo_zip = zip_path
		update_addons_xml(kodi_dir, (plugin_src, REPO_ADDON))
	publish_site(repo_zip)
	print('index.html -> %s' % os.path.basename(repo_zip))


if __name__ == '__main__':
	main()
