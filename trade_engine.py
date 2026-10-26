"""Research-only signal risk planner and deterministic paper-trading engine."""
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import math

@dataclass
class PaperPosition:
    symbol: str
    quantity: int
    entry: float
    stop: float
    target: float
    opened_at: str
    status: str = 'OPEN'
    exit_price: float = 0.0
    pnl: float = 0.0
    closed_at: str = ''

def plan_trade(signal, equity=200., risk_pct=.005, max_allocation_pct=.20, spread_pct=.003, slippage_pct=.002):
    """Conservative sizing including modeled round-trip friction. Never places orders."""
    entry, stop, target = (float(signal[k]) for k in ('entry','stop','target'))
    if not all(map(math.isfinite, (entry, stop, target, equity, risk_pct))):
        return None, 'Geçersiz sayı'
    if not (0 < stop < entry < target and equity > 0 and 0 < risk_pct <= .02):
        return None, 'Geçersiz risk/giriş/hedef'
    if signal.get('state') != 'KIRILIM TEYİDİ — İNCELE':
        return None, 'Henüz teyitli kırılım yok'
    if float(signal.get('age', 1e9)) > 2:
        return None, 'Veri güncel değil (2 dk sınırı)'
    friction = entry * (spread_pct + 2*slippage_pct)
    per_share_risk = entry-stop+friction
    qty = min(int(equity*risk_pct/per_share_risk), int(equity*max_allocation_pct/(entry*(1+slippage_pct))))
    if qty < 1: return None, 'Bütçe ve risk sınırına göre işlem büyüklüğü sıfır'
    return dict(symbol=signal['symbol'], quantity=qty, entry=entry, stop=stop, target=target,
                max_model_risk=round(qty*per_share_risk,2), notional=round(qty*entry,2),
                estimated_friction=round(qty*friction,2)), 'OK'

def open_paper(plan):
    return PaperPosition(symbol=plan['symbol'], quantity=plan['quantity'], entry=plan['entry'],
                         stop=plan['stop'], target=plan['target'], opened_at=datetime.now(timezone.utc).isoformat())

def update_paper(position, low, high, last, slippage_pct=.002):
    """Use conservative stop-first ordering when stop and target hit in same bar."""
    if position.status != 'OPEN': return position
    if low <= position.stop:
        position.exit_price = min(position.stop, float(last))*(1-slippage_pct)
        position.status = 'STOP'
    elif high >= position.target:
        position.exit_price = position.target*(1-slippage_pct)
        position.status = 'TARGET'
    else: return position
    position.pnl = round((position.exit_price-position.entry)*position.quantity, 2)
    position.closed_at = datetime.now(timezone.utc).isoformat()
    return position
