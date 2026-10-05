"""Resolve the actual Compose contract without starting a service."""
import json
import os
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]

class ComposeUITests(unittest.TestCase):
    def config(self, ui=False, env_file=None, **settings):
        env = {k: v for k, v in os.environ.items()
               if k not in ('SUB_URL', 'BIND_IP', 'PROXY_PORT', 'UI_BIND_IP', 'UI_PORT', 'UI_SECRET', 'COMPOSE_FILE')}
        env.update(settings)
        args = ['docker', 'compose', '--env-file', str(env_file or ROOT / 'env.example'), '-f', str(ROOT / 'compose.yaml')]
        if ui:
            args += ['-f', str(ROOT / 'compose.ui.yaml')]
        args += ['config', '--format', 'json']
        return json.loads(subprocess.check_output(args, env=env, text=True))['services']['clashctl']

    def test_explicit_secret_mapping_and_dotenv_precedence(self):
        import tempfile
        fixture = 'fixture-only-7d89e4c06a315bf291e836b7c94da250$literal#suffix'
        with tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR')) as tmp:
            env_file = Path(tmp) / '.env'
            for content in ('', 'UI_SECRET=\n'):
                env_file.write_text(content)
                self.assertEqual(self.config(env_file=env_file)['environment']['UI_SECRET'], '')
            env_file.write_text("UI_SECRET='" + fixture + "'\n")
            for ui in (False, True):
                # Compose escapes literal dollars when serializing reusable config.
                resolved = self.config(ui=ui, env_file=env_file)['environment']['UI_SECRET']
                self.assertEqual(resolved.replace('$$', '$'), fixture)
                self.assertEqual(self.config(ui=ui, env_file=env_file, UI_SECRET='shell-fixture')['environment']['UI_SECRET'], 'shell-fixture')

    def test_controller_address_defaults_for_unset_and_empty(self):
        import ast
        from types import SimpleNamespace
        source = (ROOT / 'container/entrypoint').read_text().split("<<'PY'\n", 1)[1].split('\nPY', 1)[0]
        tree = ast.parse(source)
        expression = next(keyword.value for node in ast.walk(tree)
                          if isinstance(node, ast.Call) for keyword in node.keywords
                          if keyword.arg == 'CONTROLLER_ADDR')
        compiled = compile(ast.Expression(expression), '<controller-address>', 'eval')
        for env, expected in [({}, '127.0.0.1:9090'), ({'CONTROLLER_BIND': ''}, '127.0.0.1:9090'),
                              ({'CONTROLLER_BIND': '0.0.0.0'}, '0.0.0.0:9090')]:
            self.assertEqual(eval(compiled, {'os': SimpleNamespace(environ=env)}), expected)

    def test_default_does_not_publish_controller(self):
        service = self.config()
        self.assertEqual(service['environment']['CONTROLLER_BIND'], '127.0.0.1')
        self.assertEqual([(p['host_ip'], p['target']) for p in service['ports']], [('127.0.0.1', 7890)])

    def test_ui_opt_in_defaults_to_host_loopback(self):
        service = self.config(ui=True)
        self.assertEqual(service['environment']['CONTROLLER_BIND'], '0.0.0.0')
        self.assertEqual([(p['host_ip'], p['target']) for p in service['ports']], [('127.0.0.1', 7890), ('127.0.0.1', 9090)])

    def test_nas_binding_does_not_change_proxy_or_storage(self):
        service = self.config(ui=True, UI_BIND_IP='192.0.2.10', UI_PORT='19090')
        self.assertEqual(service['ports'][0]['host_ip'], '127.0.0.1')
        self.assertEqual(service['ports'][1]['host_ip'], '192.0.2.10')
        self.assertEqual(str(service['ports'][1]['published']), '19090')
        self.assertEqual(service['volumes'][0]['target'], '/data')
        self.assertEqual(service['cap_drop'], ['ALL'])

if __name__ == '__main__':
    unittest.main()
