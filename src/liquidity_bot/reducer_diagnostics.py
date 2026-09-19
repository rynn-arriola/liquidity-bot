"""Decision-time exit evidence. No I/O, ledger access or execution authority."""
from .reconciler import ActionKind, exit_quote_price


def capture_reducer_decision(frame, action, *, source, apply_position):
    """Copy the exact planning frame, never a later/re-fetched book.

    Passive candidate is descriptive even for a deadline cancel/IOC; it is
    not necessarily the chosen action price. Prices use the planner's existing
    helper and matching tolerance, without calling the stateful planner again.
    """
    if frame.position == 0 or action.kind not in {
            ActionKind.CANCEL, ActionKind.PLACE_POST_ONLY, ActionKind.PLACE_IOC}:
        return None
    reducing = "offer" if frame.position > 0 else "bid"
    qty = abs(frame.position)
    book = frame.book
    bid = book.best_bid.price if book.has_side("bid") else None
    offer = book.best_offer.price if book.has_side("offer") else None
    touch = offer if reducing == "offer" else bid
    candidate = (exit_quote_price(book, reducing, frame.market.tick_size, frame.entry_price)
                 if touch is not None else None)
    reducers = [o for o in frame.working_orders if o.side == reducing]

    def matches(order, price):
        return (price is not None and abs(order.price - price) < 1e-9
                and order.remaining_qty == qty)

    at_touch = [o for o in reducers if matches(o, touch)]
    at_candidate = [o for o in reducers if matches(o, candidate)]
    preferred = at_candidate or at_touch
    target = next((o for o in frame.working_orders if o.order_id == action.order_id), None)
    target_data = None if target is None else dict(
        order_id=target.order_id, side=target.side, price=target.price,
        remaining_qty=str(target.remaining_qty),
        is_reducing=target.side == reducing,
        exact_quantity=target.remaining_qty == qty,
        at_reducing_touch=touch is not None and abs(target.price - touch) < 1e-9,
        at_passive_candidate=candidate is not None and abs(target.price - candidate) < 1e-9)
    return dict(
        schema_version=1, event="position_action_decision", enforcement="none",
        planned_at=frame.now.isoformat(), market=frame.market.slug, source=source,
        action=action.kind.value, reason=action.reason, order_id=action.order_id,
        side=action.side, action_price=action.price,
        action_qty=str(action.qty) if action.qty is not None else None,
        position=str(frame.position), apply_position=str(apply_position),
        entry_price=frame.entry_price, tick_size=frame.market.tick_size,
        position_opened_at=(frame.position_opened_at.isoformat()
                            if frame.position_opened_at is not None else None),
        book_observed_at=frame.book_observed_at.isoformat(),
        book_received_at=book.received_at.isoformat() if book.received_at else None,
        book_transact_time=book.transact_time.isoformat() if book.transact_time else None,
        book_age_seconds=round((frame.now - frame.book_observed_at).total_seconds(), 6),
        book_known=frame.book_known, state_known=frame.state_known,
        best_bid=bid, best_offer=offer, reducing_side=reducing,
        passive_candidate_price=candidate, target=target_data,
        reducing_order_count=len(reducers),
        building_order_count=len(frame.working_orders) - len(reducers),
        exact_touch_order_count=len(at_touch), exact_candidate_order_count=len(at_candidate),
        preferred_passive_order_id=preferred[0].order_id if preferred else None,
        maintenance_flatten_required=frame.maintenance_flatten_required,
        hard_stop_flatten_required=frame.hard_stop_flatten_required)


def completed_reducer_decision(record, *, at, result=None, error_type=None):
    """An ACK is not terminal cancellation or proof of a working replacement."""
    submitted = getattr(result, "submitted", None)
    cancel = getattr(result, "cancel_intent_id", None)
    outcome = ("driver_exception" if error_type else "cancel_acknowledged" if cancel
               else "place_acknowledged" if submitted else "no_acknowledgement")
    return dict(record, completed_at=at.isoformat(), outcome=outcome,
                error_type=error_type, cancel_intent_id=cancel,
                submitted_order_id=getattr(submitted, "exchange_order_id", None))
