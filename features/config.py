"""Configuration constants for the forecasting dashboard.

Edit this file to add new disease models, retune the color palette,
change the API endpoint, or adjust the daily cache TTL. Nothing here
performs I/O, so it's safe to import from any other module.
"""

# Wisconet daily-risk forecasting endpoint (UW–Madison DoIT).
API_URL = "https://connect.doit.wisc.edu/ag_forecasting_api/v2/ag_models_wrappers/wisconet_g"

# Model-metadata endpoint. Substitute ``{model_name}`` with the API's
# short model id (e.g. "tarspot") to fetch description / variables /
# model_type / risk_output / inactive_rule / version.
MODEL_INFO_URL_TEMPLATE = (
    "https://connect.doit.wisc.edu/ag_forecasting_api/v2/ag_models_wrappers/models/{model_name}"
)

# How long a cached forecast response stays fresh. 24 h means one
# network call per (forecasting_date, risk_days) pair per day.
CACHE_TTL_SECONDS = 86_400

# Model metadata barely changes, so cache it for a week.
MODEL_INFO_TTL_SECONDS = 7 * 86_400

# Sidebar label → fields used to read this model's daily forecast,
# plus the ``model_name`` used to look up the model's static metadata.
# Add a new disease by appending another entry; the sidebar, map, and
# "About this model" panel will pick it up automatically.
DISEASE_OPTIONS = {
    "Tar Spot (corn)": {
        "risk_field": "tarspot_risk",
        "class_field": "tarspot_risk_class",
        "model_name": "tarspot",
    },
    "Gray Leaf Spot (corn)": {
        "risk_field": "gls_risk",
        "class_field": "gls_risk_class",
        "model_name": "gls",
    },
    "Frogeye Leaf Spot (soybean)": {
        "risk_field": "fe_risk",
        "class_field": "fe_risk_class",
        "model_name": "fe",
    },
    "White Mold — Non-irrigated (soybean)": {
        "risk_field": "whitemold_nirr_risk",
        "class_field": "whitemold_nirr_risk_class",
        "model_name": "whitemold",
    },
    "White Mold — Irrigated 30in (soybean)": {
        "risk_field": "whitemold_irr_30in_risk",
        "class_field": "whitemold_irr_30in_class",
        "model_name": "whitemold",
    },
    "White Mold — Irrigated 15in (soybean)": {
        "risk_field": "whitemold_irr_15in_risk",
        "class_field": "whitemold_irr_15in_class",
        "model_name": "whitemold",
    },
}

# Marker colors per risk class. Keys must match the normalized class
# strings produced by ``features.data.normalize_class``.
CLASS_COLORS = {
    "Low": "#2ecc71",
    "Moderate": "#f39c12",
    "High": "#e74c3c",
    "Inactive": "#95a5a6",
    "No Risk": "#2ecc71",
    "Unknown": "#bdc3c7",
}

# Legend display order — riskiest first so the eye lands on it.
CLASS_ORDER = ["High", "Moderate", "Low", "No Risk", "Inactive", "Unknown"]

# Default map center: roughly the geographic middle of Wisconsin.
WI_CENTER = {"lat": 44.6, "lon": -89.7}

# Common Wisconet weather fields shown in the Weather tab. These are
# wiscopy field names — extend or trim to taste.
WEATHER_FIELDS = [
    "60min_air_temp_f_avg",
    "60min_air_temp_f_min",
    "60min_air_temp_f_max",
    "60min_relative_humidity_pct_avg",
    "60min_dew_point_temp_f_avg",
    "daily_rainfall_in",
    "60min_solar_rad_w_m2_avg",
    "60min_wind_speed_mph_avg",
]

# Default lookback window (days) for the weather time-series.
WEATHER_DEFAULT_DAYS = 30

# How many days of forecast to show in the Risk Trends tab. The API
# accepts 1–7 via the ``risk_days`` query param.
RISK_TRENDS_DAYS = 7
