"""Google Business review URLs per WWC location.

Hardcoded for v1 because the URLs are stable per business profile and
there's no admin UI for editing them. To change a URL, update this map
and redeploy.
"""
from typing import Optional

# location → Google Business "share" URL. The share.google links
# redirect to the practice's knowledge-graph page where Google's UI
# exposes "Write a review."
LOCATION_URLS = {
    "white_plains": "https://share.google/VWtKiIWD5JFWaZEV5",
    "arlington":    "https://share.google/ndpFsTjTtjn741Q7X",
    # "brandywine":  None yet — add when WWC sets up a Google profile
}

# Fallback used when a profile has no location, no Google URL for its
# location, or anywhere else we don't know which location to send to.
# Points to White Plains as the flagship location.
FALLBACK_URL = LOCATION_URLS["white_plains"]


def google_review_url_for(location: Optional[str]) -> str:
    if not location:
        return FALLBACK_URL
    return LOCATION_URLS.get(location, FALLBACK_URL)
