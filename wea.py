import discord
from discord.ext import commands
import sqlite3
import os
import aiohttp
import asyncio

# --- Configuration ---
TOKEN = os.getenv('DISCORD_TOKEN')
WEATHER_API_KEY = os.getenv('WEATHER_API_KEY')
DB_PATH = "/data/weather.db"

# --- Database Setup ---
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

cursor.execute('''
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        location TEXT,
        units TEXT DEFAULT 'imperial'
    )
''')
conn.commit()

# Schema migration (if needed)
try:
    cursor.execute("SELECT units FROM users LIMIT 1")
except sqlite3.OperationalError:
    cursor.execute("ALTER TABLE users ADD COLUMN units TEXT DEFAULT 'imperial'")
    conn.commit()

# --- Bot Setup ---
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='.', intents=intents)

async def get_weather_data(query, units):
    async with aiohttp.ClientSession() as session:
        
        # --- STEP 1: INITIAL LOOKUP ---
        # Determine if Zip or City
        is_zip = False
        if query.replace(" ", "").isdigit() and len(query.strip()) == 5:
            is_zip = True
            geo_url = f"http://api.openweathermap.org/geo/1.0/zip?zip={query.strip()},US&appid={WEATHER_API_KEY}"
        else:
            geo_url = f"http://api.openweathermap.org/geo/1.0/direct?q={query}&limit=1&appid={WEATHER_API_KEY}"

        async with session.get(geo_url) as geo_resp:
            if geo_resp.status != 200:
                return None
            
            geo_data = await geo_resp.json()
            
            # Handle Zip (dict) vs Direct (list)
            if isinstance(geo_data, list):
                if not geo_data: return None
                location_info = geo_data[0]
            else:
                location_info = geo_data

            lat = location_info['lat']
            lon = location_info['lon']
            
            # Default name from the first lookup
            official_name = location_info['name']
            country = location_info.get('country', 'US')

        # --- STEP 2: REVERSE GEOCODING (Fix for "Douglas County") ---
        # If we used a Zip code, the name is often a county. 
        # We query the Reverse API to get the actual City name for these coordinates.
        if is_zip:
            reverse_url = f"http://api.openweathermap.org/geo/1.0/reverse?lat={lat}&lon={lon}&limit=1&appid={WEATHER_API_KEY}"
            async with session.get(reverse_url) as rev_resp:
                if rev_resp.status == 200:
                    rev_data = await rev_resp.json()
                    if rev_data:
                        # Overwrite with the specific city name (e.g., Highlands Ranch)
                        official_name = rev_data[0]['name']

        # --- STEP 3: WEATHER ---
        weather_url = f"http://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&appid={WEATHER_API_KEY}&units={units}"
        
        async with session.get(weather_url) as weather_resp:
            if weather_resp.status == 200:
                weather_data = await weather_resp.json()
                weather_data['name'] = official_name
                weather_data['sys']['country'] = country
                return weather_data
            return None

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user}')
    await bot.change_presence(activity=discord.Game(name="the Weather"))

@bot.command(aliases=['u', 'unit'])
async def units(ctx, preference: str):
    user_id = ctx.author.id
    preference = preference.lower()
    
    if preference in ['c', 'metric', 'celsius', 'ca']:
        new_unit = 'metric'
        display = "Metric (°C, m/s)"
    elif preference in ['f', 'imperial', 'fahrenheit', 'us']:
        new_unit = 'imperial'
        display = "Imperial (°F, mph)"
    else:
        await ctx.send("❓ Please specify **metric** (C) or **imperial** (F).")
        return

    cursor.execute('''
        INSERT INTO users (user_id, units) VALUES (?, ?)
        ON CONFLICT(user_id) DO UPDATE SET units=excluded.units
    ''', (user_id, new_unit))
    conn.commit()
    await ctx.send(f"✅ Preferences updated! I will now show you weather in **{display}**.")

@bot.command(aliases=['we', 'wea'])
async def w(ctx, *, location: str = None):
    user_id = ctx.author.id

    cursor.execute('SELECT location, units FROM users WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    
    saved_location = result[0] if result else None
    user_units = result[1] if result and result[1] else 'imperial'

    if location:
        cursor.execute('''
            INSERT INTO users (user_id, location, units) VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET location=excluded.location
        ''', (user_id, location, user_units))
        conn.commit()
        search_query = location
    elif saved_location:
        search_query = saved_location
    else:
        await ctx.send("❌ **No location found!**\nPlease type `.w <ZipCode>` or `.w <City>`.")
        return

    data = await get_weather_data(search_query, user_units)
    
    if data:
        city = data['name']
        country = data['sys']['country']
        temp = data['main']['temp']
        feels_like = data['main']['feels_like']
        humidity = data['main']['humidity']
        wind_speed = data['wind']['speed']
        condition = data['weather'][0]['description'].capitalize()
        icon_code = data['weather'][0]['icon']
        
        if user_units == 'imperial':
            temp_label = "°F"
            speed_label = "mph"
        else:
            temp_label = "°C"
            speed_label = "m/s"

        embed = discord.Embed(
            title=f"Weather in {city}, {country}",
            description=f"**{condition}**",
            color=0x3498db
        )
        embed.set_thumbnail(url=f"http://openweathermap.org/img/wn/{icon_code}@2x.png")
        embed.add_field(name="Temperature", value=f"{round(temp, 1)}{temp_label}", inline=True)
        embed.add_field(name="Feels Like", value=f"{round(feels_like, 1)}{temp_label}", inline=True)
        embed.add_field(name="Humidity", value=f"{humidity}%", inline=True)
        embed.add_field(name="Wind Speed", value=f"{wind_speed} {speed_label}", inline=True)
        embed.set_footer(text=f"Requested by {ctx.author.display_name}")

        await ctx.send(embed=embed)
    else:
        await ctx.send(f"⚠️ Could not find location **'{search_query}'**.")

if __name__ == "__main__":
    bot.run(TOKEN)
