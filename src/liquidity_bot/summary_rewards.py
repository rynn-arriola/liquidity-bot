"""Account-key-scoped last-known reporting values. Never trading authority."""
from datetime import datetime
from decimal import Decimal
import json
import os
from pathlib import Path
import tempfile
from threading import Lock

_lock = Lock()


def validate(value, now):
    at = datetime.fromisoformat(value['at'])
    if at.tzinfo is None or at > now:
        raise ValueError('invalid reward observation clock')
    amounts = [Decimal(str(value[k])) for k in ('paid', 'pending')]
    if any(not n.is_finite() or n < 0 for n in amounts):
        raise ValueError('invalid reward amount')
    return dict(at=at.isoformat(), paid=str(amounts[0]), pending=str(amounts[1]))


def report_rewards(summary, now):
    """Runs only in the notification worker. Failed cache IO is nonfatal."""
    current = None
    try:
        current = validate(dict(at=summary.get('rewards_observed_at'),
            paid=summary['attributable_paid_rewards_usd'], pending=summary['pending_rewards_usd']), now)
    except (KeyError, ValueError, TypeError, ArithmeticError):
        pass
    scope = summary.get('reward_cache_scope')
    if not isinstance(scope, str) or len(scope) != 64 or any(c not in '0123456789abcdef' for c in scope):
        return current, current is None
    path = Path(str(summary['report_database']) + '.summary-rewards.json')
    with _lock:
        cached = None
        try:
            if path.is_symlink() or path.stat().st_size > 4096:
                raise ValueError('invalid cache')
            raw = json.loads(path.read_text(encoding='utf8'))
            if raw['schema'] != 1 or raw['scope'] != scope:
                raise ValueError('different reward cache scope')
            cached = validate(raw['snapshot'], now)
        except (OSError, KeyError, ValueError, TypeError, ArithmeticError):
            pass
        if current is None:
            return cached, True
        if cached and datetime.fromisoformat(cached['at']) >= datetime.fromisoformat(current['at']):
            # Never overwrite newer evidence; equal-time conflicting data is unknown.
            if (datetime.fromisoformat(cached['at']) == datetime.fromisoformat(current['at'])
                    and any(Decimal(cached[k]) != Decimal(current[k]) for k in ('paid', 'pending'))):
                return None, True
            return cached, cached != current
        temporary = None
        try:
            if path.is_symlink():
                raise ValueError('invalid cache')
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf8', dir=path.parent,
                    prefix='.summary-rewards-', delete=False) as f:
                temporary = f.name
                os.chmod(temporary, 0o600)
                json.dump(dict(schema=1, scope=scope, snapshot=current), f, allow_nan=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temporary, path)
        except (OSError, ValueError):
            pass
        finally:
            if temporary is not None:
                try:
                    Path(temporary).unlink(missing_ok=True)
                except OSError:
                    pass
        return current, False
