#!/usr/bin/env python3
import os
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRE = os.path.join(REPO, 'tools', 'host', 'artifact_preflight.py')


def run(args):
    rc = subprocess.run(['python3', PRE] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return rc.returncode, rc.stdout, rc.stderr


class ArtifactPreflightTests(unittest.TestCase):
    def test_apple_a8_rejected(self):
        rc, out, err = run(['--platform', 'apple-a8'])
        self.assertEqual(rc, 1)
        self.assertIn('UNSUPPORTED', err)

    def test_malformed_elf_rejected(self):
        fd, p = tempfile.mkstemp(suffix='.elf')
        os.write(fd, b'BAD')
        os.close(fd)
        try:
            rc, out, err = run(['--check-qemu-kernel', '--kernel-elf', p])
            self.assertEqual(rc, 1)
            self.assertIn('truncated', err)
        finally:
            os.unlink(p)

    def test_payload_oversized(self):
        fd, p = tempfile.mkstemp(suffix='.bin')
        os.write(fd, b'abc')
        os.close(fd)
        try:
            rc, out, err = run(['--check-m1-payload', '--payload-bin', p, '--max-size', '2'])
            self.assertEqual(rc, 1)
            self.assertIn('exceeds budget', err)
        finally:
            os.unlink(p)

    def test_qemu_kernel_valid(self):
        k = os.path.join(REPO, 'build', 'myos_kernel.elf')
        if not os.path.isfile(k):
            self.skipTest('kernel not built')
        rc, out, err = run(['--check-qemu-kernel', '--kernel-elf', k])
        self.assertEqual(rc, 0, err)

    def test_m1_payload_valid(self):
        k = os.path.join(REPO, 'build', 'myos_m1.bin')
        if not os.path.isfile(k):
            self.skipTest('payload not built')
        rc, out, err = run(['--check-m1-payload', '--payload-bin', k])
        self.assertEqual(rc, 0, err)


if __name__ == '__main__':
    unittest.main(verbosity=2)
