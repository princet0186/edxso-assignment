"""Outreach console pages. Each page is a plain function so it can be rendered (and smoke
tested) on its own; app/streamlit_app.py only wires them into navigation.

A thin UI over the same functions the CLI and API use: nothing here holds business rules.
"""

import altair as alt
import pandas as pd
import streamlit as st
from sqlmodel import Session

from outreach.config import load_settings
from outreach.db import get_engine
from outreach.models import ReviewStatus
from outreach.personalize.review import (
    MessageEdit,
    ReviewError,
    approve,
    approve_all_valid,
    reject,
    save_edit,
)
from outreach.reporting import (
    build_funnel,
    creator_rows,
    message_rows,
    rejection_reason_counts,
    tracker_rows,
)
from outreach.sending.dm_queue import DmNotSendableError, dm_queue, mark_dm_sent

# Single-series bars use one hue: slot 1 (blue) of the validated reference palette.
BAR_COLOR = "#2a78d6"
BAR_CORNER_RADIUS = 4
ALL_STATUSES = "All"
FLASH_KEY = "flash"


def open_session() -> Session:
    return Session(get_engine(), expire_on_commit=False)


def flash(message: str) -> None:
    """Shows a confirmation after the rerun that follows every action."""
    st.session_state[FLASH_KEY] = message


def show_flash() -> None:
    message = st.session_state.pop(FLASH_KEY, None)
    if message:
        st.success(message)


def horizontal_bars(frame: pd.DataFrame, label: str, value: str, title: str) -> alt.Chart:
    return (
        alt.Chart(frame, title=title)
        .mark_bar(color=BAR_COLOR, cornerRadiusEnd=BAR_CORNER_RADIUS)
        .encode(
            y=alt.Y(f"{label}:N", sort=None, title=None),
            x=alt.X(f"{value}:Q", title=None),
            tooltip=[alt.Tooltip(f"{label}:N"), alt.Tooltip(f"{value}:Q", format=",")],
        )
    )


def dashboard_page() -> None:
    st.title("Pipeline dashboard")
    settings = load_settings()
    with open_session() as session:
        funnel = build_funnel(session, settings)
        reasons = rejection_reason_counts(session)

    counts = {step.label: step.count for step in funnel}
    headline = ["Discovered channels", "Qualified", "Verified email found", "Messages approved"]
    for column, label in zip(st.columns(len(headline)), headline, strict=True):
        column.metric(label, f"{counts.get(label, 0):,}")

    funnel_frame = pd.DataFrame([{"Step": s.label, "Creators": s.count} for s in funnel])
    st.altair_chart(
        horizontal_bars(funnel_frame, "Step", "Creators", "Funnel: creators at each stage"),
        width="stretch",
    )
    if reasons:
        reason_frame = pd.DataFrame(
            [
                {"Reason": code.replace("_", " ").title(), "Creators": n}
                for code, n in reasons.items()
            ]
        )
        st.altair_chart(
            horizontal_bars(reason_frame, "Reason", "Creators", "Why creators were rejected"),
            width="stretch",
        )
    st.caption(f"Campaign `{settings.campaign_id}` · rules v{settings.rules_version}")


def creators_page() -> None:
    st.title("Creators")
    with open_session() as session:
        frame = pd.DataFrame(creator_rows(session))
    if frame.empty:
        st.info("No creators yet. Run `uv run outreach run` first.")
        return
    status = st.segmented_control(
        "Status", [ALL_STATUSES, "QUALIFIED", "REJECTED"], default="QUALIFIED"
    )
    if status and status != ALL_STATUSES:
        frame = frame[frame["Status"] == status]
    st.caption(f"{len(frame):,} creators")
    st.dataframe(
        frame.sort_values("Score", ascending=False, na_position="last"),
        hide_index=True,
        column_config={
            "Profile URL": st.column_config.LinkColumn("Profile URL"),
            "Score": st.column_config.NumberColumn("Score", format="%.1f"),
        },
    )


def review_page() -> None:
    st.title("Review queue")
    st.caption(
        "Only approved messages can be sent. Edits are re-validated; a message with open "
        "issues cannot be approved."
    )
    settings = load_settings()
    show_flash()
    if st.button("Approve every draft that passed validation"):
        with open_session() as session:
            flash(f"Approved {approve_all_valid(session, settings.campaign_id)} drafts.")
        st.rerun()

    with open_session() as session:
        rows = message_rows(session, settings.campaign_id)
    statuses = [ALL_STATUSES, *(status.value for status in ReviewStatus)]
    chosen = st.segmented_control("Show", statuses, default=ALL_STATUSES)
    visible = [r for r in rows if chosen in (None, ALL_STATUSES) or r["Review Status"] == chosen]
    st.caption(f"{len(visible)} messages")
    for row in visible:
        render_message(row, settings)


def render_message(row: dict, settings) -> None:
    message_id = row["Message ID"]
    with st.expander(f"{row['Name']} · {row['Review Status']} · {row['Angle']}"):
        context_column, edit_column = st.columns([1, 2])
        with context_column:
            st.markdown(f"**Why this angle:** {row['Why This Angle']}")
            st.markdown(f"**Referenced video:** {row['Referenced Video'] or '—'}")
            st.markdown(f"**Signals used:** {row['Signals Used']}")
            st.markdown(f"**Email:** {row['Email']}  \n**Instagram:** {row['Instagram'] or '—'}")
            st.caption(f"{row['Model']} · {row['Prompt Version']} · {row['Attempts']} attempt(s)")
            if row["Validation Issues"]:
                st.warning(row["Validation Issues"])
            if row["Similarity Warning"]:
                st.info(row["Similarity Warning"])
        with edit_column, st.form(key=f"message-{message_id}"):
            subject = st.text_input("Subject", row["Email Subject"])
            body = st.text_area("Email body", row["Email Body"], height=200)
            dm = st.text_area("Instagram DM", row["Instagram DM"], height=90)
            st.caption(f"Email {row['Email Words']} words · DM {row['DM Words']} words")
            save_col, approve_col, reject_col = st.columns(3)
            if save_col.form_submit_button("Save edit"):
                handle_edit(message_id, MessageEdit(subject, body, dm), settings)
            if approve_col.form_submit_button("Approve", type="primary"):
                handle_action(message_id, approve, "Approved")
            if reject_col.form_submit_button("Reject"):
                handle_action(message_id, reject, "Rejected")


def handle_edit(message_id: int, edit: MessageEdit, settings) -> None:
    try:
        with open_session() as session:
            issues = save_edit(session, message_id, edit, settings)
    except ReviewError as error:
        st.error(str(error))
        return
    flash("Saved; passes validation." if not issues else f"Saved with issues: {'; '.join(issues)}")
    st.rerun()


def handle_action(message_id: int, action, verb: str) -> None:
    try:
        with open_session() as session:
            action(session, message_id)
    except ReviewError as error:
        st.error(str(error))
        return
    flash(f"{verb} message #{message_id}.")
    st.rerun()


def dm_queue_page() -> None:
    st.title("Instagram DM queue")
    st.caption(
        "Instagram does not allow automated cold DMs, so these are sent by hand: copy the DM, "
        "open the profile, send it from the brand account, then mark it sent."
    )
    settings = load_settings()
    show_flash()
    with open_session() as session:
        items = dm_queue(session, settings.campaign_id)
    if not items:
        st.info("No approved messages yet. Approve drafts in the review queue first.")
        return
    pending = [item for item in items if item.sent_at is None]
    st.caption(f"{len(pending)} to send · {len(items) - len(pending)} sent")
    for item in items:
        render_dm(item, settings.campaign_id)


def render_dm(item, campaign_id: str) -> None:
    sent_label = f"sent {item.sent_at:%Y-%m-%d %H:%M} UTC" if item.sent_at else "not sent"
    with st.expander(f"{item.name} · {sent_label}"):
        st.code(item.dm, language=None, wrap_lines=True)
        if not item.instagram_url:
            st.warning("No Instagram handle was found for this creator; the DM cannot be sent.")
            return
        st.link_button("Open Instagram profile", item.instagram_url)
        if item.sent_at is None and st.button("Mark as sent", key=f"dm-{item.creator_id}"):
            try:
                with open_session() as session:
                    mark_dm_sent(session, campaign_id, item.creator_id)
            except DmNotSendableError as error:
                st.error(str(error))
                return
            flash(f"Marked the DM to {item.name} as sent.")
            st.rerun()


def tracker_page() -> None:
    st.title("Outreach tracker")
    settings = load_settings()
    with open_session() as session:
        frame = pd.DataFrame(tracker_rows(session, settings.campaign_id))
    if frame.empty:
        st.info("Nothing generated yet.")
        return
    sent = frame["Sent"].str.startswith("Yes").sum()
    dm_sent = frame["Instagram DM Sent"].str.startswith("Yes").sum()
    for column, (label, value) in zip(
        st.columns(3),
        [("Messages generated", len(frame)), ("Emails sent", sent), ("DMs sent", dm_sent)],
        strict=True,
    ):
        column.metric(label, f"{value:,}")
    st.dataframe(frame, hide_index=True)
