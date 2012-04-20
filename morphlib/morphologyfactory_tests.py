# Copyright (C) 2012  Codethink Limited
# 
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; version 2 of the License.
# 
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
# 
# You should have received a copy of the GNU General Public License along
# with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.


import unittest

from morphlib.morph2 import Morphology
from morphlib.morphologyfactory import (MorphologyFactory,
                                        AutodetectError,
                                        NotcachedError)
from morphlib.remoterepocache import CatFileError

class FakeRemoteRepoCache(object):
    def cat_file(self, reponame, sha1, filename):
        if filename.endswith('.morph'):
           return '''{
                "name": "remote-foo",
                "kind": "chunk",
                "build-system": "bar"
            }'''
        return 'text'

class FakeLocalRepo(object):
    def cat(self, sha1, filename):
        if filename.endswith('.morph'):
            return '''{
                "name": "local-foo",
                "kind": "chunk",
                "build-system": "bar"
            }'''
        return 'text'

class FakeLocalRepoCache(object):
    def __init__(self, lr):
        self.lr = lr
    def has_repo(self, reponame):
        return True
    def get_repo(self, reponame):
        return self.lr

class MorphologyFactoryTests(unittest.TestCase):
    def setUp(self):
        self.lr = FakeLocalRepo()
        self.lrc = FakeLocalRepoCache(self.lr)
        self.rrc = FakeRemoteRepoCache()
        self.mf = MorphologyFactory(self.lrc, self.rrc)
        self.lmf = MorphologyFactory(self.lrc, None)

    def nolocalfile(self, *args):
        raise IOError('File not found')
    def noremotefile(self, *args):
        raise CatFileError('reponame', 'ref', 'filename')
    def nolocalmorph(self, *args):
        if args[-1].endswith('.morph'):
            raise IOError('File not found')
        return 'text'
    def noremotemorph(self, *args):
        if args[-1].endswith('.morph'):
            raise CatFileError('reponame', 'ref', 'filename')
        return 'text'

    def doesnothaverepo(self, reponame):
        return False

    def test_gets_morph_from_local_repo(self):
        morph = self.mf.get_morphology('reponame', 'sha1',
                                       'foo.morph')
        self.assertEqual('local-foo', morph['name'])

    def test_gets_morph_from_remote_repo(self):
        self.lrc.has_repo = self.doesnothaverepo
        morph = self.mf.get_morphology('reponame', 'sha1',
                                       'foo.morph')
        self.assertEqual('remote-foo', morph['name'])

    def test_autodetects_local_morphology(self):
        self.lr.cat = self.nolocalmorph
        morph = self.mf.get_morphology('reponame', 'sha1', 
                                       'assumed-local.morph')
        self.assertEqual('assumed-local', morph['name'])

    def test_autodetects_remote_morphology(self):
        self.lrc.has_repo = self.doesnothaverepo
        self.rrc.cat_file = self.noremotemorph
        morph = self.mf.get_morphology('reponame', 'sha1',
                                       'assumed-remote.morph')
        self.assertEqual('assumed-remote', morph['name'])

    def test_raises_error_when_fails_detect_locally(self):
        self.lr.cat = self.nolocalfile
        self.assertRaises(AutodetectError, self.mf.get_morphology,
                          'reponame', 'sha1', 'unreached.morph')

    def test_raises_error_when_fails_detect_remotely(self):
        self.lrc.has_repo = self.doesnothaverepo
        self.rrc.cat_file = self.noremotefile
#        self.mf.get_morphology('reponame', 'sha1', 'unreached.morph')
        self.assertRaises(AutodetectError, self.mf.get_morphology,
                          'reponame', 'sha1', 'unreached.morph')

    def test_looks_locally_with_no_remote(self):
        morph = self.lmf.get_morphology('reponame', 'sha1', 
                                        'foo.morph')
        self.assertEqual('local-foo', morph['name'])

    def test_autodetects_locally_with_no_remote(self):
        self.lr.cat = self.nolocalmorph
        morph = self.lmf.get_morphology('reponame', 'sha1',
                                        'assumed-local.morph')
        self.assertEqual('assumed-local', morph['name'])

    def test_fails_when_local_not_cached_and_no_remote(self):
        self.lrc.has_repo = self.doesnothaverepo
        self.assertRaises(NotcachedError, self.lmf.get_morphology,
                          'reponame', 'sha1', 'unreached.morph')