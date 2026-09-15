import os
import json
import requests
from flask import Flask, request, jsonify
from hyundai_kia_connect_api import VehicleManager, ClimateRequestOptions, Token
from hyundai_kia_connect_api.const import OTP_NOTIFY_TYPE
from hyundai_kia_connect_api.exceptions import AuthenticationError

app = Flask(__name__)

# =========================
# Environment Variables
# =========================
USERNAME = os.environ.get("KIA_USERNAME")
PASSWORD = os.environ.get("KIA_PASSWORD")
PIN = os.environ.get("KIA_PIN")
SECRET_KEY = os.environ.get("SECRET_KEY")
VEHICLE_ID = os.environ.get("VEHICLE_ID")  # Optional

missing = []
if not USERNAME:
    missing.append("KIA_USERNAME")
if not PASSWORD:
    missing.append("KIA_PASSWORD")
if not PIN:
    missing.append("KIA_PIN")
if not SECRET_KEY:
    missing.append("SECRET_KEY")

if missing:
    raise ValueError(f"Missing environment variables: {', '.join(missing)}")

# =========================
# Vehicle Manager
# =========================
vehicle_manager = VehicleManager(
    region=3,  # North America
    brand=1,   # KIA
    username=USERNAME,
    password=PASSWORD,
    pin=str(PIN)
)

# =========================
# Session persistence (Upstash Redis)
# =========================
# Vercel serverless functions don't keep the Python process alive between
# requests, so the login token would normally vanish on every cold start.
# We stash it in a free Upstash Redis database and reload it whenever a
# fresh process comes up with no token in memory.
UPSTASH_URL = os.environ.get("UPSTASH_REDIS_REST_URL")
UPSTASH_TOKEN_SECRET = os.environ.get("UPSTASH_REDIS_REST_TOKEN")
TOKEN_STORE_KEY = "kia_session_token"


def _store_configured():
    return bool(UPSTASH_URL and UPSTASH_TOKEN_SECRET)


def save_token_to_store():
    """Persist the current login token so it survives cold starts."""
    if not _store_configured() or vehicle_manager.token is None:
        return
    try:
        payload = json.dumps(vehicle_manager.token.to_dict())
        requests.post(
            f"{UPSTASH_URL}/set/{TOKEN_STORE_KEY}",
            headers={"Authorization": f"Bearer {UPSTASH_TOKEN_SECRET}"},
            data=payload,
            timeout=10
        )
    except Exception:
        pass  # best effort - don't break the request over a caching hiccup


def restore_token_from_store():
    """Load a previously saved token, if any. Returns True if one was restored."""
    if not _store_configured():
        return False
    try:
        resp = requests.get(
            f"{UPSTASH_URL}/get/{TOKEN_STORE_KEY}",
            headers={"Authorization": f"Bearer {UPSTASH_TOKEN_SECRET}"},
            timeout=10
        )
        result = resp.json().get("result")
        if not result:
            return False
        vehicle_manager.token = Token.from_dict(json.loads(result))
        vehicle_manager.initialize_vehicles()
        return True
    except Exception:
        return False

# =========================
# Helper Functions
# =========================
def authorize_request():
    return request.headers.get("Authorization") == SECRET_KEY


def ensure_authenticated():
    """
    Makes sure we have a live Kia session, in this order:
    1. Already logged in this process? nothing to do.
    2. Fresh cold start with nothing in memory? try restoring a saved session.
    3. Token exists but expired? refresh it, then re-save the new one.
    4. None of that works? a real re-login via /request_otp is needed.
    """
    if vehicle_manager.token is None:
        restore_token_from_store()

    try:
        vehicle_manager.check_and_refresh_token()
        save_token_to_store()
    except AuthenticationError as e:
        raise AuthenticationError(
            "Kia authentication failed. "
            "Hit /request_otp then /verify_otp again to start a new session."
        ) from e


def refresh_and_sync():
    """
    Refresh token and sync vehicle state
    """
    ensure_authenticated()
    vehicle_manager.update_all_vehicles_with_cached_state()


def get_vehicle_id():
    """
    Return VEHICLE_ID if provided, otherwise
    dynamically select the first vehicle.
    """
    if VEHICLE_ID:
        return VEHICLE_ID

    vehicles = vehicle_manager.vehicles
    if not vehicles:
        raise ValueError("No vehicles found on the Kia account.")

    first_vehicle_id = next(iter(vehicles.keys()))
    return first_vehicle_id


# =========================
# Logging
# =========================
@app.before_request
def log_request_info():
    print(f"Incoming request: {request.method} {request.path}")


# =========================
# Routes
# =========================
@app.route("/", methods=["GET"])
def root():
    return jsonify({
        "status": "OK",
        "service": "Kia Vehicle Control API"
    }), 200


@app.route("/auth_status", methods=["GET"])
def auth_status():
    if not authorize_request():
        return jsonify({"error": "Unauthorized"}), 403

    try:
        ensure_authenticated()
        return jsonify({"status": "authenticated"}), 200
    except AuthenticationError as e:
        return jsonify({
            "status": "authentication_failed",
            "message": str(e)
        }), 401


@app.route("/request_otp", methods=["POST"])
def request_otp():
    """
    Kicks off Kia's OTP requirement. First attempts a normal login,
    which either succeeds outright or comes back asking for OTP.
    If OTP is required, this then tells Kia to send the code by
    email or SMS.
    """
    if not authorize_request():
        return jsonify({"error": "Unauthorized"}), 403

    body = request.get_json(silent=True) or {}
    method = body.get("method", "email").lower()  # "email" or "phone"
    notify_type = OTP_NOTIFY_TYPE.SMS if method == "phone" else OTP_NOTIFY_TYPE.EMAIL

    try:
        result = vehicle_manager.login()

        if result is True:
            # No OTP needed this time, already logged in
            vehicle_manager.update_all_vehicles_with_cached_state()
            save_token_to_store()
            return jsonify({
                "status": "logged_in",
                "message": "No OTP was required, login succeeded directly."
            }), 200

        # result is an OTPRequest, stored internally by the library
        vehicle_manager.send_otp(notify_type)
        return jsonify({
            "status": "otp_sent",
            "method": method,
            "message": f"Check your {method} for a Kia verification code, then hit /verify_otp with it."
        }), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/verify_otp", methods=["POST"])
def verify_otp():
    """
    Submits the code Kia sent you to actually complete the login.
    """
    if not authorize_request():
        return jsonify({"error": "Unauthorized"}), 403

    body = request.get_json(silent=True) or {}
    otp_code = body.get("otp_code")

    if not otp_code:
        return jsonify({"error": "Missing 'otp_code' in request body"}), 400

    try:
        vehicle_manager.verify_otp_and_complete_login(otp_code)
        save_token_to_store()

        vehicles = [
            {"name": v.name, "id": v.id, "model": v.model, "year": v.year}
            for v in vehicle_manager.vehicles.values()
        ]

        return jsonify({
            "status": "verified",
            "vehicles": vehicles
        }), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/list_vehicles", methods=["GET"])
def list_vehicles():
    if not authorize_request():
        return jsonify({"error": "Unauthorized"}), 403

    try:
        refresh_and_sync()

        vehicles = vehicle_manager.vehicles
        if not vehicles:
            return jsonify({"error": "No vehicles found"}), 404

        vehicle_list = [
            {
                "name": v.name,
                "id": v.id,
                "model": v.model,
                "year": v.year
            }
            for v in vehicles.values()
        ]

        return jsonify({
            "status": "success",
            "vehicles": vehicle_list
        }), 200

    except AuthenticationError as e:
        return jsonify({
            "error": "Authentication failed",
            "details": str(e),
            "action": "Open Kia app and complete 2FA"
        }), 401

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/start_climate", methods=["POST"])
def start_climate():
    if not authorize_request():
        return jsonify({"error": "Unauthorized"}), 403

    try:
        refresh_and_sync()
        vehicle_id = get_vehicle_id()

        climate_options = ClimateRequestOptions(
            set_temp=72,
            duration=10
        )

        result = vehicle_manager.start_climate(vehicle_id, climate_options)

        return jsonify({
            "status": "climate_started",
            "result": result
        }), 200

    except AuthenticationError as e:
        return jsonify({
            "error": "Authentication failed",
            "details": str(e),
            "action": "Open Kia app and complete 2FA"
        }), 401

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/stop_climate", methods=["POST"])
def stop_climate():
    if not authorize_request():
        return jsonify({"error": "Unauthorized"}), 403

    try:
        refresh_and_sync()
        vehicle_id = get_vehicle_id()

        result = vehicle_manager.stop_climate(vehicle_id)

        return jsonify({
            "status": "climate_stopped",
            "result": result
        }), 200

    except AuthenticationError as e:
        return jsonify({
            "error": "Authentication failed",
            "details": str(e),
            "action": "Open Kia app and complete 2FA"
        }), 401

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/unlock_car", methods=["POST"])
def unlock_car():
    if not authorize_request():
        return jsonify({"error": "Unauthorized"}), 403

    try:
        refresh_and_sync()
        vehicle_id = get_vehicle_id()

        result = vehicle_manager.unlock(vehicle_id)

        return jsonify({
            "status": "car_unlocked",
            "result": result
        }), 200

    except AuthenticationError as e:
        return jsonify({
            "error": "Authentication failed",
            "details": str(e),
            "action": "Open Kia app and complete 2FA"
        }), 401

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/lock_car", methods=["POST"])
def lock_car():
    if not authorize_request():
        return jsonify({"error": "Unauthorized"}), 403

    try:
        refresh_and_sync()
        vehicle_id = get_vehicle_id()

        result = vehicle_manager.lock(vehicle_id)

        return jsonify({
            "status": "car_locked",
            "result": result
        }), 200

    except AuthenticationError as e:
        return jsonify({
            "error": "Authentication failed",
            "details": str(e),
            "action": "Open Kia app and complete 2FA"
        }), 401

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# =========================
# App Entry
# =========================
if __name__ == "__main__":
    print("Starting Kia Vehicle Control API...")
    app.run(host="0.0.0.0", port=8080)
