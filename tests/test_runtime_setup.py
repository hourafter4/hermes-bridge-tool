"""Runtime provisioning integration keeps admin credentials separate and registration stable."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from hermes_bridge_tool import runtime_setup, setup


class RuntimeSetupTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.config = self.root / 'config.json'
        self.config.write_text(json.dumps({'ssh_host': 'admin-alias', 'webui_ssh': True,
            'security': {'mode': 'monitor'}, 'native_mcp_command': ['previous-native'], 'custom': 'preserved'}))
        env = patch.dict(os.environ, {'HERMES_BRIDGE_TOOL_CONFIG': str(self.config)}, clear=True)
        env.start(); self.addCleanup(env.stop)

    def test_runtime_keys_override_alias_user_and_preserve_policy(self):
        with patch.object(runtime_setup, 'generate_runtime_key', side_effect=['forward-public', 'native-public']), \
                patch.object(runtime_setup, 'provision_runtime_ssh', return_value={'ok': True}) as provision:
            runtime_setup.harden('admin-alias', native_command=['/home/hermes/.local/bin/hermes', 'mcp', 'serve'])
        saved = json.loads(self.config.read_text())
        self.assertEqual(saved['ssh_user'], 'hermes-bridge')
        self.assertIn('forward-ed25519', saved['ssh_identity_file'])
        self.assertEqual(saved['security'], {'mode': 'monitor'})
        self.assertEqual(saved['custom'], 'preserved')
        command = saved['native_mcp_command']
        self.assertEqual(command[command.index('-l') + 1], 'hermes')
        self.assertIn('native-ed25519', command[command.index('-i') + 1])
        self.assertNotIn('sudo', command)
        self.assertNotIn('root', command)
        self.assertEqual(provision.call_args.kwargs['ports'], [8642, 8787])

    def test_provision_failure_keeps_previous_configuration(self):
        before = self.config.read_bytes()
        with patch.object(runtime_setup, 'generate_runtime_key', return_value='public'), \
                patch.object(runtime_setup, 'provision_runtime_ssh', side_effect=ValueError('failed')):
            with self.assertRaises(ValueError):
                runtime_setup.harden('admin-alias', native_command=['/path/hermes', 'mcp', 'serve'])
        self.assertEqual(before, self.config.read_bytes())

    def test_existing_native_requires_explicit_command_before_any_provisioning(self):
        with patch.object(runtime_setup, 'generate_runtime_key') as generate:
            with self.assertRaisesRegex(ValueError, 'native-command'):
                runtime_setup.harden('admin-alias')
        generate.assert_not_called()

    def test_registration_uses_stable_launcher_for_verified_runtime(self):
        runtime = self.root / 'runtime' / 'bin'; runtime.mkdir(parents=True)
        executable = runtime / 'hermes-bridge-tool'; executable.touch()
        stable = self.root / 'bin'; stable.mkdir()
        (stable / 'hermes-bridge-tool').symlink_to(executable)
        with patch.dict(os.environ, {'UV_TOOL_BIN_DIR': str(stable)}), \
                patch.object(setup.sys, 'executable', str(runtime / 'python')):
            self.assertEqual(setup.bridge_command(), [str(stable / 'hermes-bridge-tool'), 'mcp'])
