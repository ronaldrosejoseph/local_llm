"""
Secure Hugging Face token management using OS-native keyring storage.

- save_hf_token(token): verifies then saves to macOS Keychain
- load_hf_token(): retrieves from keyring and logs in via huggingface_hub
- verify_token(token): checks validity against the HuggingFace API
- delete_hf_token(): removes the stored token
"""

import os
import sys
import keyring
import huggingface_hub

SERVICE_NAME = "local-llm-chat"
ACCOUNT_NAME = "hf-token"


def verify_token(token: str) -> tuple[bool, str]:
    """Check whether a HuggingFace token is valid.

    Returns (is_valid, username_or_error_message).
    """
    if not token or not token.strip():
        return False, "Token is empty"

    token = token.strip()

    # Temporarily disable offline mode for verification
    import huggingface_hub.constants
    original_offline = huggingface_hub.constants.HF_HUB_OFFLINE
    huggingface_hub.constants.HF_HUB_OFFLINE = False
    try:
        info = huggingface_hub.whoami(token=token)
        username = info.get("name", "unknown")
        return True, username
    except Exception as e:
        err_str = str(e)
        if "401" in err_str or "Invalid token" in err_str:
            return False, "Invalid token — check your HuggingFace token and try again"
        if "403" in err_str:
            return False, "Token is valid but lacks required permissions"
        return False, str(e)
    finally:
        huggingface_hub.constants.HF_HUB_OFFLINE = original_offline


def save_hf_token(token: str) -> tuple[bool, str]:
    """Verify and save a HuggingFace token to the system keyring.

    Returns (success, message).
    """
    token = token.strip()
    if not token:
        return False, "Token is empty"

    # Verify first
    valid, msg = verify_token(token)
    if not valid:
        return False, f"Token verification failed: {msg}"

    # Save to keyring
    try:
        keyring.set_password(SERVICE_NAME, ACCOUNT_NAME, token)
        # Temporarily disable offline mode for login verification
        import huggingface_hub.constants
        original_offline = huggingface_hub.constants.HF_HUB_OFFLINE
        huggingface_hub.constants.HF_HUB_OFFLINE = False
        try:
            huggingface_hub.login(token=token)
        finally:
            huggingface_hub.constants.HF_HUB_OFFLINE = original_offline
        return True, f"Token saved and verified for user: {msg}"
    except keyring.errors.KeyringError as e:
        return False, f"Keyring error (macOS Keychain unavailable?): {e}"
    except Exception as e:
        return False, f"Failed to save token: {e}"


def load_hf_token() -> str | None:
    """Load the HF token from the keyring and log in.

    Returns the token if found, None otherwise.
    """
    try:
        token = keyring.get_password(SERVICE_NAME, ACCOUNT_NAME)
        if token:
            # Set environment variable for subprocesses (worker.py, etc.)
            os.environ["HF_TOKEN"] = token
            
            import huggingface_hub.constants
            original_offline = huggingface_hub.constants.HF_HUB_OFFLINE
            huggingface_hub.constants.HF_HUB_OFFLINE = False
            try:
                huggingface_hub.login(token=token)
            except Exception as e:
                print(f"HF auth: login verification skipped/failed: {e}", file=sys.stderr)
            finally:
                huggingface_hub.constants.HF_HUB_OFFLINE = original_offline
                    
            print("HF auth: token loaded from keyring and activated", file=sys.stderr)
        return token
    except keyring.errors.KeyringError as e:
        print(f"HF auth: keyring unavailable — {e}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"HF auth: failed to load token — {e}", file=sys.stderr)
        return None


def delete_hf_token() -> bool:
    """Remove the stored HF token from the keyring."""
    try:
        keyring.delete_password(SERVICE_NAME, ACCOUNT_NAME)
        os.environ.pop("HF_TOKEN", None)
        print("HF auth: token deleted from keyring", file=sys.stderr)
        return True
    except keyring.errors.PasswordDeleteError:
        # Token wasn't stored
        return True
    except keyring.errors.KeyringError as e:
        print(f"HF auth: keyring error during delete — {e}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"HF auth: failed to delete token — {e}", file=sys.stderr)
        return False


def has_token() -> bool:
    """Check if a token is stored in the keyring."""
    try:
        return keyring.get_password(SERVICE_NAME, ACCOUNT_NAME) is not None
    except Exception:
        return False
