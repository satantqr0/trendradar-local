"""Initialize a local admin credential, then start the upstream scheduler."""
import os
from pathlib import Path
from secrets import token_urlsafe
import sys
from tempfile import NamedTemporaryFile

private = Path('/app/focus_private')
private.mkdir(mode=0o700,exist_ok=True)
private.chmod(0o700)
key = private/'admin.key'
if not key.exists():
    with NamedTemporaryFile(dir=private, mode='w', encoding='utf-8', delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(token_urlsafe(27)+'\n')
        stream.flush()
        os.fsync(stream.fileno())
    try:
        os.link(temporary,key)
    except FileExistsError:
        pass
    finally:
        temporary.unlink()
key.chmod(0o600)
os.execv('/entrypoint.sh',['/entrypoint.sh',*sys.argv[1:]])
