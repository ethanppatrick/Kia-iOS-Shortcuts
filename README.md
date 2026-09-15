# Kia-iOS-Shortcuts

This project provides a simple API to control your Kia vehicle using the Hyundai Kia Connect API. It includes features such as starting and stopping the climate control, locking and unlocking the car, and listing vehicles.

---
## Intro

This uses the Kia API written in Python. It was developed specifically for iOS shortcuts, though it may also work on Android OS. This allows for shortcuts to:

- Lock Vehicle  
- Unlock Vehicle  
- Start Climate  
- Stop Climate  

This uses the following Python package: [Hyundai Kia Connect API](https://github.com/Hyundai-Kia-Connect/hyundai_kia_connect_api).

---

## Setup

### 1. Create a GitHub repo and Vercel account
- If you don’t have a GitHub or Vercel account, create one.
- Fork this repo or clone it to your own GitHub account.

### 2. Set up Environment Variables
In your project, set up the following environment variables:
- `KIA_USERNAME`: Your Kia username.
- `KIA_PASSWORD`: Your Kia password.
- `KIA_PIN`: Your Kia PIN.
- `SECRET_KEY`: Your Secret Key (this is a custom password you create. Add whatever value you'd like)
- `VEHICLE_ID`: Your Vehicle ID (needed if you have more than one vehicle tied to your account)

### 3. Deploy on Vercel
Once the repo is on GitHub, follow these steps to deploy it on Vercel:
1. Go to [Vercel](https://vercel.com/) and log in with your GitHub account.
2. Click on **New Project** and choose your repository.
3. Set up your environment variables in Vercel’s dashboard:
    - `KIA_USERNAME`: (value)
    - `KIA_PASSWORD`: (value)
    - `KIA_PIN`: (value)
    - `SECRET_KEY`: (value) (your own custom password for more security)
    - `VEHICLE_ID`: (value)

### 4. Deploy the project.

### 5. Create IOS Shortcuts
You can create an iOS Shortcut to interact with your Kia Vehicle Control API easily. Follow these steps to set up your Shortcut:

    1. Open the Shortcuts app on your iPhone.
    2. Tap the "+" to create a new shortcut.
    3. Tap "Add Action".
    4. In the search bar, type "Get Contents of URL" and select it.
    5. Set the following options for the "Get Contents of URL" action:
        - URL: Enter the URL of your deployed API endpoint (e.g., https://your-api-vercel.app/start_climate).
            - each url will end with the proper endpoint: 
                    - /unlock_car
                    - /lock_car
                    - /start_climate
                    - /stop_climate
        - Method: Choose POST (or GET if the endpoint requires GET).
        - Headers: Tap "Add New Field" and enter:
            - Key: Authorization
            - Value: YourCustomSecretKeyHere (replace this with your actual secret key).
    6. In the search bar, type "Show Result" and select it. (shows Contents of URL)
    7. Tap the drop-down arrow at the top of the shortcut to Rename and Choose Icon.
    8. Tap Done to save the shortcut.
    9. Run the Shortcut: When you run the shortcut, it will send a request to your API, performing the action you configured (e.g., starting the climate control or unlocking the car).

## Notes

The API requires your **region**. By default, it is set to the USA. If you are outside the US, update it using the following region codes:

REGIONS = {
    1: REGION_EUROPE,
    2: REGION_CANADA,
    3: REGION_USA,
    4: REGION_CHINA,
    5: REGION_AUSTRALIA }



The climate command requires a Climate Request Option. By default, it is set to 72°F for 10 minutes, but you can modify this based on your preferences.

---

## License

This project is licensed under the MIT License – see the LICENSE file for details.

## OTP Login (required as of 2026)

Kia added mandatory OTP verification (a code sent by email or SMS) for USA account
logins. The original version of this project predates that change and will fail
with an error like:

```
hyundai_kia_connect_api - No session id returned in login. Response: {"status":{"statusCode":1,"errorType":3,"errorCode":9789,"errorMessage":"Invalid Request"}}
```

If you're forking this repo fresh, here's what changed and what you need to do.

### 1. Delete `uv.lock` if it's present

An old `uv.lock` in this repo pinned `hyundai-kia-connect-api` at version `3.32.7`,
which was released before OTP support existed in the library. If your Vercel
deployment is respecting that lockfile over `requirements.txt`, no code change will
fix your login, since it's running ancient dependency code underneath. Delete the
file:

```bash
git rm uv.lock
git commit -m "remove stale lockfile"
git push
```

`requirements.txt` now pins a version with OTP support:

```
hyundai-kia-connect-api>=4.31.0
```

### 2. New endpoints: `/request_otp` and `/verify_otp`

Two new routes handle the login handshake:

- `POST /request_otp` with body `{"method": "email"}` or `{"method": "phone"}`
  attempts a login. If Kia doesn't require OTP that time, you're logged in
  immediately. If it does, Kia sends you a code.
- `POST /verify_otp` with body `{"otp_code": "123456"}` completes the login using
  that code, and returns your vehicle list on success.

Both are protected by the same `SECRET_KEY` header as every other route.

Run them back to back, `/request_otp` first, then `/verify_otp` within a minute
or so of getting the code.

### 3. Session persistence via Upstash Redis

Vercel serverless functions don't keep anything in memory between requests, each
one can spin up as a brand-new process. That means a successful login would
normally vanish the next time you hit any endpoint. To fix that, the login token
is now saved to a small free Redis database (via [Upstash](https://upstash.com))
right after login, and restored automatically on every request if it isn't
already in memory.

You'll need two more environment variables, both free from Upstash's console
after creating a database:

- `UPSTASH_REDIS_REST_URL`
- `UPSTASH_REDIS_REST_TOKEN`

Without these two set, the app still works, it'll just need a fresh
`/request_otp` → `/verify_otp` login almost every time Vercel cold-starts your
function, which in practice is often.

### Summary of all environment variables now required

- `KIA_USERNAME`
- `KIA_PASSWORD`
- `KIA_PIN`
- `SECRET_KEY`
- `VEHICLE_ID` (optional, but recommended once you know it)
- `UPSTASH_REDIS_REST_URL`
- `UPSTASH_REDIS_REST_TOKEN`
