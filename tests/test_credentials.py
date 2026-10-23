import io
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from mcd_client import McdError, _token_path, load_token, save_token
from mcd_rights import main


class CredentialBoundaryTests(unittest.TestCase):
    def test_explicit_encrypted_file_overrides_environment_token(self):
        with patch.dict(os.environ, {"MCD_MCP_TOKEN": "wrong-synthetic-token"}), patch("mcd_client._token_path") as path, patch("mcd_client._dpapi", return_value=b"synthetic-test-token") as decrypt:
            path.return_value.read_bytes.return_value = b"synthetic-encrypted-bytes"
            self.assertEqual(load_token("chosen-private-file"), "synthetic-test-token")
            path.assert_called_once_with("chosen-private-file")
            decrypt.assert_called_once_with(b"synthetic-encrypted-bytes", False)

    def test_missing_explicit_file_never_falls_back_to_environment_or_default(self):
        with patch.dict(os.environ, {"MCD_MCP_TOKEN": "synthetic-test-token"}), patch("mcd_client._token_path") as path:
            path.return_value.read_bytes.side_effect = FileNotFoundError
            with self.assertRaises(McdError):
                load_token("chosen-private-file")
            path.assert_called_once_with("chosen-private-file")

    def test_custom_save_keeps_dpapi_encryption(self):
        with patch("mcd_client._token_path") as path, patch("mcd_client._dpapi", return_value=b"synthetic-encrypted") as encrypt:
            save_token("synthetic-test-token", "chosen-private-file")
            encrypt.assert_called_once_with(b"synthetic-test-token", True)
            path.return_value.write_bytes.assert_called_once_with(b"synthetic-encrypted")

    def test_cli_passes_explicit_file_to_loader_before_network(self):
        with patch("mcd_rights.load_token", side_effect=McdError("synthetic missing file")) as loader, patch("mcd_rights.McdClient") as client, patch("sys.stderr", new_callable=io.StringIO):
            self.assertEqual(main(["--live", "--token-file", "chosen-private-file", "--focus", "points"]), 1)
            loader.assert_called_once_with("chosen-private-file")
            client.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "Windows path semantics")
    def test_relative_custom_path_is_rejected(self):
        with self.assertRaisesRegex(McdError, "绝对路径"):
            _token_path("private/token.dpapi")

    def test_hidden_file_does_not_claim_user_has_never_configured_token(self):
        with patch.dict(os.environ, {}, clear=True), patch("mcd_client._token_path") as path:
            path.return_value.read_bytes.side_effect = FileNotFoundError
            with self.assertRaisesRegex(McdError, "宿主文件隔离"):
                load_token()

    def test_denied_file_exits_before_any_network_and_without_traceback(self):
        with patch.dict(os.environ, {}, clear=True), patch("mcd_client._token_path") as path, patch("mcd_rights.McdClient") as client, patch("sys.stderr", new_callable=io.StringIO) as err:
            path.return_value.read_bytes.side_effect = PermissionError("private filesystem detail")
            self.assertEqual(main(["--live", "--focus", "points", "--plan", "no"]), 1)
            client.assert_not_called()
            self.assertIn("无法读取加密凭证", err.getvalue())
            self.assertNotIn("private filesystem detail", err.getvalue())
            self.assertNotIn("Traceback", err.getvalue())

    def test_missing_windows_path_is_configuration_error(self):
        with patch.dict(os.environ, {}, clear=True), patch("mcd_client.os.name", "nt"):
            with self.assertRaisesRegex(McdError, "缺少LOCALAPPDATA"):
                _token_path()

    def test_environment_token_keeps_precedence_without_accessing_file(self):
        with patch.dict(os.environ, {"MCD_MCP_TOKEN": "synthetic-test-token"}), patch("mcd_client._token_path") as path:
            self.assertEqual(load_token(), "synthetic-test-token")
            path.assert_not_called()

    def test_malformed_decrypted_text_is_safe_configuration_error(self):
        with patch.dict(os.environ, {}, clear=True), patch("mcd_client._token_path"), patch("mcd_client._dpapi", return_value=b"\xff"):
            with self.assertRaisesRegex(McdError, "无法解码"):
                load_token()


if __name__ == "__main__":
    unittest.main()
