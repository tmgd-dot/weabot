import discord
import aiohttp
import asyncio
import json
import os
import re
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────
DISCORD_TOKEN   = os.environ["DISCORD_TOKEN"]
OWM_API_KEY     = os.environ["OWM_API_KEY"]
PREFIXES        = (".we", ".wea", ".wx")
USER_DATA_FILE  = Path("/data/users.json")

# US state name → abbreviation lookup (for flexible city/state parsing)
US_STATES = {
    "alabama":"AL","alaska":"AK","arizona":"AZ","arkansas":"AR","california":"CA",
    "colorado":"CO","connecticut":"CT","delaware":"DE","florida":"FL","georgia":"GA",
    "hawaii":"HI","idaho":"ID","illinois":"IL","indiana":"IN","iowa":"IA",
    "kansas":"KS","kentucky":"KY","louisiana":"LA","maine":"ME","maryland":"MD",
    "massachusetts":"MA","michigan":"MI","minnesota":"MN","mississippi":"MS",
    "missouri":"MO","montana":"MT","nebraska":"NE","nevada":"NV","new hampshire":"NH",
    "new jersey":"NJ","new mexico":"NM","new york":"NY","north carolina":"NC",
    "north dakota":"ND","ohio":"OH","oklahoma":"OK","oregon":"OR","pennsylvania":"PA",
    "rhode island":"RI","south carolina":"SC","south dakota":"SD","tennessee":"TN",
    "texas":"TX","utah":"UT","vermont":"VT","virginia":"VA","washington":"WA",
    "west virginia":"WV","wisconsin":"WI","wyoming":"WY","district of columbia":"DC",
}

# Canadian province name → abbreviation
CA_PROVINCES = {
    "alberta":"AB","british columbia":"BC","manitoba":"MB","new brunswick":"NB",
    "newfoundland":"NL","newfoundland and labrador":"NL","northwest territories":"NT",
    "nova scotia":"NS","nunavut":"NU","ontario":"ON","prince edward island":"PE",
    "quebec":"QC","québec":"QC","saskatchewan":"SK","yukon":"YT",
}

# ── User data helpers ─────────────────────────────────────────────────────────
def load_users() -> dict:
    if USER_DATA_FILE.exists():
        with open(USER_DATA_FILE) as f:
            return json.load(f)
    return {}

def save_users(data: dict):
    USER_DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(USER_DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)

def get_user_location(user_id: str) -> str | None:
    return load_users().get(user_id)

def set_user_location(user_id: str, location: str):
    data = load_users()
    data[user_id] = location
    save_users(data)

# ── Location string normalizer ────────────────────────────────────────────────
def normalize_location(raw: str) -> str:
    """
    Accepts:
      - ZIP code:           "80104"
      - city state abbr:    "cleveland oh" / "cleveland, oh"
      - city full state:    "cleveland, ohio"
      - city province:      "vancouver, bc" / "vancouver bc"
    Returns a clean query string for OWM geocoding API.
    """
    raw = raw.strip()

    # ZIP code (5-digit US)
    if re.fullmatch(r"\d{5}", raw):
        return raw  # pass straight to zip endpoint

    # Remove extra commas/spaces and split on comma or multiple spaces
    parts = [p.strip() for p in re.split(r",\s*|\s{2,}", raw)]

    if len(parts) == 1:
        # Try splitting on last word as potential state/province abbr
        words = raw.split()
        if len(words) >= 2:
            possible_region = words[-1].upper()
            city = " ".join(words[:-1])
            # Check if last word looks like a US/CA abbreviation (2 letters)
            if len(possible_region) == 2:
                parts = [city, possible_region]

    if len(parts) >= 2:
        city = parts[0].title()
        region = parts[1].strip().lower()

        # Expand full state/province name to abbreviation
        abbr = (
            US_STATES.get(region)
            or CA_PROVINCES.get(region)
            or region.upper()  # already an abbreviation
        )

        # Determine country code
        country = "CA" if (CA_PROVINCES.get(region) or region.upper() in CA_PROVINCES.values()) else "US"
        return f"{city},{abbr},{country}"

    return raw.title()

def is_zip(location: str) -> bool:
    return bool(re.fullmatch(r"\d{5}", location.strip()))

# ── OWM API calls ─────────────────────────────────────────────────────────────
BASE = "https://api.openweathermap.org"

async def fetch_weather(session: aiohttp.ClientSession, location: str) -> dict | None:
    """Resolve location → lat/lon → current weather. Returns dict or None on error."""
    key = OWM_API_KEY

    # Step 1: geocode
    if is_zip(location):
        geo_url = f"{BASE}/geo/1.0/zip"
        params  = {"zip": f"{location},US", "appid": key}
    else:
        geo_url = f"{BASE}/geo/1.0/direct"
        params  = {"q": location, "limit": 1, "appid": key}

    async with session.get(geo_url, params=params) as r:
        if r.status != 200:
            return None
        geo = await r.json()

    if not geo:
        return None

    if is_zip(location):
        lat, lon = geo["lat"], geo["lon"]
        display_name = f"{geo.get('name', location)}"
        country = geo.get("country", "")
    else:
        g = geo[0]
        lat, lon = g["lat"], g["lon"]
        state   = g.get("state", "")
        country = g.get("country", "")
        display_name = g.get("name", location)
        if state:
            display_name += f", {state}"

    # Step 2: current weather (imperial units)
    wx_url = f"{BASE}/data/2.5/weather"
    async with session.get(wx_url, params={"lat": lat, "lon": lon, "units": "imperial", "appid": key}) as r:
        if r.status != 200:
            return None
        wx = await r.json()

    return {
        "display_name": display_name,
        "country":      country,
        "temp":         round(wx["main"]["temp"]),
        "feels_like":   round(wx["main"]["feels_like"]),
        "temp_min":     round(wx["main"]["temp_min"]),
        "temp_max":     round(wx["main"]["temp_max"]),
        "humidity":     wx["main"]["humidity"],
        "description":  wx["weather"][0]["description"].title(),
        "icon":         wx["weather"][0]["icon"],
        "wind_speed":   round(wx["wind"]["speed"]),
        "wind_deg":     wx["wind"].get("deg", 0),
        "visibility":   wx.get("visibility", None),
        "clouds":       wx["clouds"]["all"],
    }

def wind_direction(deg: int) -> str:
    dirs = ["N","NNE","NE","ENE","E","ESE","SE","SSE","S","SSW","SW","WSW","W","WNW","NW","NNW"]
    return dirs[round(deg / 22.5) % 16]

def weather_emoji(icon: str) -> str:
    mapping = {
        "01d": "☀️",  "01n": "🌙",
        "02d": "🌤️", "02n": "🌤️",
        "03d": "☁️",  "03n": "☁️",
        "04d": "☁️",  "04n": "☁️",
        "09d": "🌧️", "09n": "🌧️",
        "10d": "🌦️", "10n": "🌦️",
        "11d": "⛈️",  "11n": "⛈️",
        "13d": "❄️",  "13n": "❄️",
        "50d": "🌫️", "50n": "🌫️",
    }
    return mapping.get(icon, "🌡️")

def build_embed(data: dict) -> discord.Embed:
    emoji = weather_emoji(data["icon"])
    title = f"{emoji}  {data['display_name']}"
    if data["country"] and data["country"] != "US":
        title += f", {data['country']}"

    embed = discord.Embed(
        title       = title,
        description = f"**{data['description']}**",
        color       = 0x5865F2,
    )
    embed.set_thumbnail(url=f"https://openweathermap.org/img/wn/{data['icon']}@2x.png")

    embed.add_field(name="🌡️ Temp",       value=f"{data['temp']}°F", inline=True)
    embed.add_field(name="🤔 Feels Like", value=f"{data['feels_like']}°F", inline=True)
    embed.add_field(name="💧 Humidity",   value=f"{data['humidity']}%", inline=True)

    embed.add_field(name="⬇️ Low",  value=f"{data['temp_min']}°F", inline=True)
    embed.add_field(name="⬆️ High", value=f"{data['temp_max']}°F", inline=True)
    embed.add_field(name="☁️ Cloud Cover", value=f"{data['clouds']}%", inline=True)

    wind_str = f"{data['wind_speed']} mph {wind_direction(data['wind_deg'])}"
    embed.add_field(name="💨 Wind", value=wind_str, inline=True)

    if data["visibility"] is not None:
        vis_mi = round(data["visibility"] / 1609, 1)
        embed.add_field(name="👁️ Visibility", value=f"{vis_mi} mi", inline=True)

    embed.set_footer(text="Powered by OpenWeatherMap  •  .wx set <location> to update your default")
    return embed

# ── Bot setup ─────────────────────────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
client  = discord.Client(intents=intents)

HELP_TEXT = """
**Weather Bot Commands**
`.wx` — show weather for your saved location
`.wx <location>` — show weather for any location (doesn't change your default)
`.wx set <location>` — save a new default location

**Location formats accepted:**
• ZIP code: `.wx 80104`
• City + state abbr: `.wx cleveland oh` or `.wx cleveland, oh`
• City + full state: `.wx cleveland, ohio`
• City + province: `.wx vancouver, bc`
""".strip()

@client.event
async def on_ready():
    print(f"Logged in as {client.user} (ID: {client.user.id})")

@client.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    content = message.content.strip()
    lower   = content.lower()

    # Check for matching prefix
    matched_prefix = None
    for prefix in PREFIXES:
        if lower.startswith(prefix):
            matched_prefix = prefix
            break

    if not matched_prefix:
        return

    # Extract the argument after the prefix
    arg = content[len(matched_prefix):].strip()
    user_id = str(message.author.id)

    async with message.channel.typing():
        async with aiohttp.ClientSession() as session:

            # ── .wx help ──────────────────────────────────────────────────────
            if arg.lower() == "help":
                await message.channel.send(HELP_TEXT)
                return

            # ── .wx set <location> ────────────────────────────────────────────
            if arg.lower().startswith("set "):
                raw_loc = arg[4:].strip()
                if not raw_loc:
                    await message.channel.send("❓ Please provide a location. Example: `.wx set denver, co`")
                    return

                location = normalize_location(raw_loc)
                data = await fetch_weather(session, location)
                if data is None:
                    await message.channel.send(
                        f"❌ Couldn't find **{raw_loc}**. "
                        "Try a ZIP code or `City, ST` format."
                    )
                    return

                set_user_location(user_id, location)
                embed = build_embed(data)
                embed.set_footer(text=f"✅ Default location saved: {raw_loc.title()}  •  Powered by OpenWeatherMap")
                await message.channel.send(embed=embed)
                return

            # ── .wx <location> (one-off lookup) ──────────────────────────────
            if arg:
                location = normalize_location(arg)
                data = await fetch_weather(session, location)
                if data is None:
                    await message.channel.send(
                        f"❌ Couldn't find **{arg}**. "
                        "Try a ZIP code, `City, ST`, or `City, Province`."
                    )
                    return
                await message.channel.send(embed=build_embed(data))
                return

            # ── .wx (no argument) ─────────────────────────────────────────────
            saved = get_user_location(user_id)
            if not saved:
                await message.channel.send(
                    "👋 Looks like you haven't set a default location yet!\n"
                    "Use `.wx set <location>` to save one. For example:\n"
                    "• `.wx set 80104`\n"
                    "• `.wx set denver, co`\n"
                    "• `.wx set cleveland, ohio`\n\n"
                    "You can also look up any location right now with `.wx <location>`."
                )
                return

            data = await fetch_weather(session, saved)
            if data is None:
                await message.channel.send(
                    "❌ Couldn't retrieve weather for your saved location. "
                    "Try updating it with `.wx set <location>`."
                )
                return
            await message.channel.send(embed=build_embed(data))

client.run(DISCORD_TOKEN)
