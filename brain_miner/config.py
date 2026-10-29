import json
import os
from pathlib import Path


def load_env(path=Path('.env')):
    """Minimal dotenv loader; existing process variables take precedence."""
    if not path.exists():
        return
    for line in path.read_text(encoding='utf-8-sig').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        key, sep, value = line.partition('=')
        if not sep or not key.strip().replace('_', '').isalnum():
            raise ValueError('Invalid .env entry; expected KEY=value')
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        os.environ.setdefault(key.strip(), value)


def settings(path='settings.json'):
    value = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    required = {'instrumentType', 'region', 'universe', 'delay', 'decay', 'language'}
    if not isinstance(value, dict) or not required.issubset(value):
        raise ValueError('Incomplete simulation settings')
    if value['delay'] not in (0, 1) or not isinstance(value['decay'], int) or value['decay'] < 0:
        raise ValueError('Invalid delay or decay')
    return value


def require(names):
    missing = [n for n in names if not os.getenv(n)]
    if missing:
        raise ValueError('Missing configuration: ' + ', '.join(missing))
