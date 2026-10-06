"""Print a password hash for PLATFORM_PASSWORD_HASH. Usage: python -m scripts.hash_password"""

import getpass

from app.security import hash_password

if __name__ == "__main__":
    print(hash_password(getpass.getpass("Parol: ")))
