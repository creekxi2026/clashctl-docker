"""Failure paths use an in-memory Docker double; never contact the daemon."""
import ast
import copy
import json
from pathlib import Path
from types import SimpleNamespace
import unittest

SOURCE = Path(__file__).with_name('dashboard.py')


def load_fixture():
    tree = ast.parse(SOURCE.read_text())
    acceptance = tree.body.pop()
    assert isinstance(acceptance, ast.Try)
    # Load definitions only; exercise the same finally block as acceptance.
    tree.body.append(ast.FunctionDef(name='finish', args=ast.arguments(
        posonlyargs=[], args=[], kwonlyargs=[], kw_defaults=[], defaults=[]),
        body=acceptance.finalbody, decorator_list=[]))
    ns = {'__file__': str(SOURCE)}
    exec(compile(ast.fix_missing_locations(tree), str(SOURCE), 'exec'), ns)
    return ns


class DashboardCleanupTests(unittest.TestCase):
    def setUp(self):
        self.ns = load_fixture()
        self.owner = self.ns['OWNER']
        self.project = self.owner + '-dotenv'
        self.resources = {}
        self.volumes = {'volume-one', 'volume-two'}
        self.calls = []
        self.errors = {}
        self.saved = []
        self.ns['volume'] = 'volume-one'
        self.ns['ledger']['volumes'] = sorted(self.volumes)
        self.ns['docker'] = self.docker
        self.ns['record'] = lambda: self.saved.append(copy.deepcopy(self.ns['ledger']))
        self.ns['wait_ready'] = lambda cid: cid
        self.ns['subprocess'] = SimpleNamespace(run=self.create, check_output=self.ps)
        self.failure = None

    def add(self, cid, owner=None, project=None, registered=False):
        self.resources[cid] = {'Id': cid, 'Config': {'Labels': {
            'test.owner': owner or self.owner,
            'com.docker.compose.project': project or self.project},
            'Env': ['UI_SECRET=public-fixture']}}
        if registered:
            self.ns['containers'].append(cid)
        return cid

    def create(self, args, **kwargs):
        self.add('a' * 64)
        self.before_create = copy.deepcopy(self.ns['ledger'])
        return SimpleNamespace(returncode=int(self.failure == 'create'), stderr='create failure')

    def ps(self, args, **kwargs):
        if self.failure == 'ps':
            raise RuntimeError('ps failure')
        return 'a' * 64

    def docker(self, *args):
        self.calls.append(args)
        if args in self.errors:
            raise RuntimeError(self.errors[args])
        if args[0] == 'ps':
            self.assertIn('label=test.owner=' + self.owner, args)
            self.assertIn('label=com.docker.compose.project=' + self.project, args)
            self.assertIn('--no-trunc', args)
            return '\n'.join(self.resources)
        if args[0] == 'inspect':
            if self.failure == 'inspect':
                self.failure = None
                raise RuntimeError('inspect failure')
            return json.dumps([self.resources[args[1]]])
        if args[0] == 'rm':
            del self.resources[args[2]]
        elif args[:2] == ('volume', 'inspect'):
            return json.dumps([{'Name': args[2], 'Labels': {'test.owner': self.owner}}])
        elif args[:2] == ('volume', 'rm'):
            self.volumes.remove(args[2])
        return ''

    def test_create_ps_and_inspect_failures_recover_unregistered_container(self):
        for failure in ('create', 'ps', 'inspect'):
            with self.subTest(failure=failure):
                self.setUp()
                self.failure = failure
                with self.assertRaises((AssertionError, RuntimeError)):
                    self.ns['start_compose_fixture']('public-fixture')
                self.ns['finish']()
                self.assertEqual(self.resources, {})
                self.assertEqual(self.volumes, set())
                self.assertIn(self.project, self.before_create.get('compose_projects', []))
                self.assertTrue(self.saved[-1]['cleaned'])

    def track_project(self):
        self.ns['ledger']['compose_projects'] = [self.project]

    def test_discovery_error_still_cleans_registered_resources(self):
        self.track_project()
        cid = self.add('b' * 64, registered=True)
        original = self.ns['docker']
        self.ns['docker'] = lambda *args: (_ for _ in ()).throw(RuntimeError('discovery failure')) if args[0] == 'ps' else original(*args)
        with self.assertRaisesRegex(Exception, 'discovery failure'):
            self.ns['finish']()
        self.assertNotIn(cid, self.resources)
        self.assertEqual(self.volumes, set())
        self.assertFalse(self.ns['ledger'].get('cleaned', False))

    def test_resource_errors_accumulate_and_do_not_interrupt_cleanup(self):
        self.track_project()
        first = self.add('b' * 64, registered=True)
        second = self.add('c' * 64, registered=True)
        self.add('d' * 64, registered=True)
        self.errors[('inspect', first)] = 'inspect failed'
        self.errors[('rm', '-f', second)] = 'remove failed'
        self.errors[('volume', 'rm', 'volume-one')] = 'volume failed'
        with self.assertRaises(Exception) as caught:
            self.ns['finish']()
        for message in ('inspect failed', 'remove failed', 'volume failed'):
            self.assertIn(message, str(caught.exception))
        self.assertEqual(set(self.resources), {first, second})
        self.assertEqual(self.volumes, {'volume-one'})
        self.assertFalse(self.ns['ledger'].get('cleaned', False))
        self.assertFalse(self.saved[-1].get('cleaned', False))

    def test_refuse_foreign_owner_project_or_mismatched_id(self):
        for mismatch in ('owner', 'project', 'id'):
            with self.subTest(mismatch=mismatch):
                self.setUp()
                self.track_project()
                cid = self.add('e' * 64, owner='foreign' if mismatch == 'owner' else None,
                               project='foreign' if mismatch == 'project' else None)
                if mismatch == 'id':
                    self.resources[cid]['Id'] = 'f' * 64
                with self.assertRaises(Exception):
                    self.ns['finish']()
                self.assertIn(cid, self.resources)
                self.assertNotIn(('rm', '-f', cid), self.calls)
                self.assertEqual(self.volumes, set())
                self.assertFalse(self.ns['ledger'].get('cleaned', False))

    def test_registered_compose_container_still_requires_project_match(self):
        self.track_project()
        cid = self.add('a' * 64, project='foreign', registered=True)
        with self.assertRaises(Exception):
            self.ns['finish']()
        self.assertNotIn(('rm', '-f', cid), self.calls)

    def test_discovered_inspect_failure_does_not_block_other_resources(self):
        self.track_project()
        cid = self.add('a' * 64)
        self.add('b' * 64)
        self.errors[('inspect', cid)] = 'discovered inspect failure'
        with self.assertRaisesRegex(Exception, 'discovered inspect failure'):
            self.ns['finish']()
        self.assertEqual(set(self.resources), {cid})
        self.assertEqual(self.volumes, set())
        self.assertFalse(self.ns['ledger'].get('cleaned', False))

    def test_success_deduplicates_discovered_registered_container(self):
        self.track_project()
        cid = self.add('a' * 64, registered=True)
        self.ns['finish']()
        self.assertEqual(self.calls.count(('rm', '-f', cid)), 1)
        self.assertTrue(self.ns['ledger']['cleaned'])


if __name__ == '__main__':
    unittest.main()
