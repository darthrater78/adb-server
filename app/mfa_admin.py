"""Two-factor sign-in, from the server's shell — for when you can't get
past the code in the browser:

    docker compose exec app python mfa_admin.py status
    docker compose exec app python mfa_admin.py unlock   # clear the lockout after too many wrong codes
    docker compose exec app python mfa_admin.py reset    # turn two-factor off, sign everyone out

Being able to run a command in the app container is the proof of ownership
here: it already means full control of the server and its data."""
import sys

import audit
import db
import mfa

USAGE = "usage: python mfa_admin.py status|unlock|reset"


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[1] not in ("status", "unlock", "reset"):
        print(USAGE, file=sys.stderr)
        return 2
    db.init_db()
    command = argv[1]
    if command == "unlock":
        mfa.unlock()
        audit.record("mfa_unlocked", "from the server shell", None)
        print("Unlocked: two-factor codes are accepted again.")
    elif command == "reset":
        mfa.disable()
        db.bump_session_epoch()
        audit.record("mfa_reset", "from the server shell; all sessions signed out", None)
        print("Two-factor sign-in is off, recovery codes and trusted browsers are gone, and every session is "
              "signed out. Sign in with the password, then set it up again in Settings.")
    if not mfa.enabled():
        print("Status: two-factor sign-in is off.")
    else:
        locked = mfa.locked_for()
        print(f"Status: on. {db.recovery_codes_left()} recovery codes left, "
              f"{len(db.list_trusted_browsers())} trusted browsers"
              + (f", locked for {locked} more seconds." if locked else ", not locked."))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
