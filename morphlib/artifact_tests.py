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

import morphlib


class ArtifactTests(unittest.TestCase):

    def setUp(self):
        morph = morphlib.morph2.Morphology(
                '''
                {
                    "chunk": "chunk",
                    "kind": "chunk",
                    "artifacts": {
                        "chunk-runtime": [
                            "usr/bin",
                            "usr/sbin",
                            "usr/lib",
                            "usr/libexec"
                        ],
                        "chunk-devel": [
                            "usr/include"
                        ]
                    }
                }
                ''')
        self.source = morphlib.source.Source(
                'repo', 'ref', 'sha1', morph, 'chunk.morph')
        self.cache_key = 'cachekey'
        self.artifact_name = 'chunk-runtime'
        self.artifact = morphlib.artifact.Artifact(
                self.source, self.artifact_name, self.cache_key)

    def test_constructor_sets_source(self):
        self.assertEqual(self.artifact.source, self.source)

    def test_constructor_sets_name(self):
        self.assertEqual(self.artifact.name, self.artifact_name)

    def test_constructor_sets_cache_key(self):
        self.assertEqual(self.artifact.cache_key, self.cache_key)