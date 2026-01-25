#!/usr/bin/env python3
"""
Streamlit app to fetch unanswered Google reviews (Places API or Business Profile API)
and post standard replies based on star rating.

Secrets expected in Streamlit:
- google_api_key: (string) Maps/Places API key (read-only, limited reviews)
- business_service_account: (string or dict) Service account JSON or base64-encoded JSON to use with Business Profile API
"""
import json
import base64
from typing import List, Dict, Optional

import streamlit as st
import requests

# Optional Google client libraries (used only if business_profile credentials are provided)
try:
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    GOOGLE_CLIENT_AVAILABLE = True
except Exception:
    GOOGLE_CLIENT_AVAILABLE = False

st.set_page_config(page_title="Salasar Services — Review Replier", layout="wide")
st.title("Salasar Services — Google Reviews Reply Assistant")

st.markdown(
    """
This app fetches Google reviews (either via the Places Details API using an API key, or via the Business Profile API using a service account)
and helps you prepare and post replies. Posting replies requires Business Profile API credentials with `https://www.googleapis.com/auth/business.manage`.
"""
)

# -----------------------
# Helpers & reply templates
# -----------------------
def gen_reply_by_rating(first_name: str, rating: int, extra: str = "") -> str:
    star_part = f"{rating} star" if rating == 1 else f"{rating} stars"
    if rating >= 5:
        start = f"Hi {first_name}, Thank you for the {star_part} review. We are delighted you had an excellent experience."
    elif rating == 4:
        start = f"Hi {first_name}, Thank you for the {star_part} review. We appreciate the feedback."
    elif rating == 3:
        start = f"Hi {first_name}, Thank you for the {star_part} review. We appreciate your honest feedback and will work to improve."
    elif rating == 2:
        start = f"Hi {first_name}, We're sorry your experience was not ideal. Thank you for the {star_part} review — we'll use this to improve."
    else:  # 1 star
        start = f"Hi {first_name}, We're very sorry you had a bad experience. Thank you for the {star_part} review — please contact us so we can make it right."

    if extra:
        return f"{start} {extra}\n\nTeam Salasar Services"
    else:
        return f"{start}\n\nTeam Salasar Services"


# -----------------------
# Places API (read-only)
# -----------------------
def get_reviews_places(place_id: str, api_key: str) -> List[Dict]:
    url = "https://maps.googleapis.com/maps/api/place/details/json"
    params = {"place_id": place_id, "fields": "name,rating,reviews", "key": api_key}
    r = requests.get(url, params=params, timeout=20)
    r.raise_for_status()
    data = r.json()
    if data.get("status") != "OK":
        raise RuntimeError(f"Places API error: {data.get('status')} - {data.get('error_message')}")
    result = data.get("result", {})
    reviews = result.get("reviews", [])
    normalized = []
    for rev in reviews:
        normalized.append({
            "reviewId": rev.get("author_url", "") or "",  # Places doesn't provide reviewId
            "author_name": rev.get("author_name", ""),
            "rating": rev.get("rating", 0),
            "text": rev.get("text", ""),
            "time": rev.get("time", None),
            "reply": None
        })
    return normalized


# -----------------------
# Business Profile API (full access)
# -----------------------
def create_business_profile_service_from_service_account(sa_info: Dict, scopes: List[str]):
    if not GOOGLE_CLIENT_AVAILABLE:
        raise RuntimeError("googleapiclient or google-auth packages are not installed. See requirements.txt.")
    creds = service_account.Credentials.from_service_account_info(sa_info, scopes=scopes)
    service = build("mybusiness", "v4", credentials=creds, cache_discovery=False)
    return service


def list_accounts_and_locations(service) -> Dict[str, List[Dict]]:
    accounts = {}
    resp = service.accounts().list().execute()
    for acct in resp.get("accounts", []):
        acct_name = acct["name"]
        locs = []
        loc_resp = service.accounts().locations().list(parent=acct_name).execute()
        for loc in loc_resp.get("locations", []):
            locs.append({"name": loc.get("name"), "storeCode": loc.get("storeCode")})
        accounts[acct_name] = locs
    return accounts


def list_reviews_businessprofile(service, location_name: str) -> List[Dict]:
    reviews = []
    resp = service.accounts().locations().reviews().list(parent=location_name).execute()
    for r in resp.get("reviews", []):
        # starRating in My Business API is like "FIVE" or "ONE". Convert to number if possible.
        star = r.get("starRating")
        rating = None
        if isinstance(star, str):
            mapping = {"ONE": 1, "TWO": 2, "THREE": 3, "FOUR": 4, "FIVE": 5}
            rating = mapping.get(star.upper())
        reviews.append({
            "reviewId": r.get("reviewId"),
            "name": r.get("name"),
            "author_name": r.get("reviewer", {}).get("displayName", ""),
            "rating": rating,
            "text": r.get("comment"),
            "createTime": r.get("createTime"),
            "reply": r.get("reviewReply", {}).get("comment") if r.get("reviewReply") else None
        })
    return reviews


def post_reply_businessprofile(service, review_name: str, comment: str) -> Dict:
    body = {"comment": comment}
    resp = service.accounts().locations().reviews().reply(name=review_name, body=body).execute()
    return resp


# -----------------------
# Streamlit UI & flow
# -----------------------
secrets = st.secrets
google_api_key = secrets.get("google_api_key") if secrets else None
st.write("Places API key present:", bool(google_api_key))

sa_json_raw = secrets.get("business_service_account") if secrets else None
service_account_info = None
if sa_json_raw:
    try:
        if isinstance(sa_json_raw, dict):
            service_account_info = sa_json_raw
        else:
            try:
                decoded = base64.b64decode(sa_json_raw).decode("utf-8")
                service_account_info = json.loads(decoded)
            except Exception:
                service_account_info = json.loads(sa_json_raw)
    except Exception as e:
        st.error(f"Could not parse service account JSON from secrets: {e}")
        service_account_info = None

st.write("Business Profile Service Account present:", bool(service_account_info))

st.header("Fetch method")
mode = st.radio("Fetch method:", ("Places API (API key, limited, read-only)", "Business Profile API (full, requires service account)"))

reviews: List[Dict] = []

if mode.startswith("Places"):
    if not google_api_key:
        st.warning("No `google_api_key` found in st.secrets. Add it to fetch reviews with the Places API.")
    else:
        place_id = st.text_input("Google Place ID (place_id)")
        if st.button("Fetch reviews (Places API)"):
            try:
                with st.spinner("Fetching reviews from Places API..."):
                    reviews = get_reviews_places(place_id.strip(), google_api_key)
                st.success(f"Fetched {len(reviews)} reviews (Places API returns only recent reviews).")
            except Exception as e:
                st.error(f"Error: {e}")
else:
    if not service_account_info:
        st.warning("No Business Profile service account in st.secrets['business_service_account']. Required to post replies.")
    else:
        if not GOOGLE_CLIENT_AVAILABLE:
            st.error("Missing google-api-python-client or google-auth packages. Install them (see requirements.txt).")
        else:
            if st.button("Connect & list accounts/locations"):
                try:
                    with st.spinner("Connecting..."):
                        scopes = ["https://www.googleapis.com/auth/business.manage"]
                        service = create_business_profile_service_from_service_account(service_account_info, scopes=scopes)
                        accounts = list_accounts_and_locations(service)
                        st.session_state["bp_service"] = service
                        st.session_state["bp_accounts"] = accounts
                        st.success("Connected.")
                except Exception as e:
                    st.error(f"Error connecting: {e}")

            accounts = st.session_state.get("bp_accounts") or {}
            if accounts:
                acct_choice = st.selectbox("Select account", options=list(accounts.keys()))
                locs = accounts.get(acct_choice, [])
                loc_choice = st.selectbox("Select location", options=[l["name"] for l in locs])
                if st.button("Fetch reviews for selected location"):
                    try:
                        with st.spinner("Fetching reviews..."):
                            service = st.session_state.get("bp_service")
                            reviews = list_reviews_businessprofile(service, loc_choice)
                            st.success(f"Fetched {len(reviews)} reviews.")
                    except Exception as e:
                        st.error(f"Error fetching reviews: {e}")

# Show and prepare replies
if reviews:
    st.header("Unanswered reviews & replies")
    unanswered = [r for r in reviews if not r.get("reply")]
    st.write(f"Total fetched: {len(reviews)} — Unanswered: {len(unanswered)}")

    default_extra = st.text_area("Optional extra text to append to every reply", value="", height=80)
    to_post = []

    for i, rev in enumerate(unanswered):
        st.markdown("---")
        author = rev.get("author_name") or "Customer"
        rating = rev.get("rating") or 4
        text = rev.get("text") or ""
        st.write(f"Review #{i+1} — {author} ({rating} stars)")
        st.write(text)
        first_name = author.split()[0] if isinstance(author, str) and author.strip() else "there"
        preview = gen_reply_by_rating(first_name, int(rating), extra=default_extra)
        custom = st.text_area(f"Reply text (editable) — review #{i+1}", value=preview, key=f"reply_{i}", height=140)
        post_checkbox = st.checkbox("Post reply for this review", key=f"post_{i}", value=True)
        if post_checkbox:
            to_post.append({"review": rev, "reply_text": custom})

    st.markdown("---")
    st.write("Selected to post:", len(to_post))

    if st.button("Post selected replies now"):
        service = st.session_state.get("bp_service")
        if not service:
            st.error("No Business Profile service available. Please connect using the Business Profile flow.")
        else:
            results = []
            for item in to_post:
                rev = item["review"]
                reply_text = item["reply_text"]
                review_id = rev.get("reviewId")
                if not review_id:
                    results.append({"status": "skipped", "reason": "no reviewId"})
                    continue
                # Construct review resource name. Prefer 'name' if present.
                if rev.get("name"):
                    review_name = rev.get("name")
                else:
                    # Fallback: try to use the single connected location if available
                    accounts = st.session_state.get("bp_accounts", {})
                    review_name = None
                    for acct, locs in accounts.items():
                        if len(locs) == 1:
                            review_name = f"{locs[0]['name']}/reviews/{review_id}"
                            break
                if not review_name:
                    results.append({"status": "skipped", "reason": "cannot construct review resource name"})
                    continue
                try:
                    with st.spinner("Posting reply..."):
                        resp = post_reply_businessprofile(service, review_name, reply_text)
                    results.append({"status": "posted", "reviewId": review_id, "response": resp})
                except Exception as e:
                    results.append({"status": "failed", "reviewId": review_id, "error": str(e)})
            st.subheader("Results")
            st.json(results)

st.markdown("---")
st.header("Notes")
st.markdown(
    """
- An API key (Places) can only fetch a limited set of recent reviews and cannot post replies.
- To post replies programmatically you must use Business Profile API credentials with `business.manage` scope.
- Do not commit service account JSON to your repo. Store it in Streamlit secrets or other secret manager.
"""
)
