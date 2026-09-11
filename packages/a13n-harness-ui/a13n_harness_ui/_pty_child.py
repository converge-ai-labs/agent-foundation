"""Exec-only PTY child: never run Python fork hooks inside the server process."""

import os
import sys


def main() -> None:
    # The parent passes the slave FD. login_tty creates the session, acquires its
    # controlling terminal, duplicates stdin/out/err and closes the original FD.
    os.login_tty(int(sys.argv[1]))
    shell = sys.argv[2]
    os.execv(shell, [shell, "-i"])


if __name__ == "__main__":
    main()
