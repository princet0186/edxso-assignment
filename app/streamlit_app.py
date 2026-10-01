"""Outreach console — run with: uv run streamlit run app/streamlit_app.py"""

import streamlit as st
from console_pages import creators_page, dashboard_page, review_page

from outreach.db import get_engine, init_db

st.set_page_config(page_title="Outreach Console", page_icon=":material/campaign:", layout="wide")
init_db(get_engine())
navigation = st.navigation(
    [
        st.Page(dashboard_page, title="Dashboard", icon=":material/monitoring:", default=True),
        st.Page(creators_page, title="Creators", icon=":material/groups:"),
        st.Page(review_page, title="Review queue", icon=":material/rate_review:"),
    ]
)
navigation.run()
