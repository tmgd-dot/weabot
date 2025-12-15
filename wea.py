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

# --- Database Setup & Migration ---
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

# 1. Create table if it doesn't exist
cursor.execute('''
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        location TEXT,
        units TEXT DEFAULT 'imperial'
    )
''')
conn.commit()

# 2. Migration Check: Ensure existing databases get the 'units' column
try:
    cursor.execute("SELECT units FROM users LIMIT 1")
except sqlite3.OperationalError:
    print("Database: Upgrading schema to include 'units' column...")
    cursor.execute("ALTER TABLE users ADD COLUMN units TEXT DEFAULT 'imperial'")
    conn.commit()
    print("Database: Upgrade complete.")

# --- Bot Setup ---
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='.', intents=intents)

async def get_weather_data(query, units):
    async with aiohttp.ClientSession() as session:
        
        # --- STEP 1: GEOCODING ---
        if query.replace(" ", "").isdigit() and len(query.strip()) == 5:
            geo_url = f"http://api.openweathermap.org/geo/1.0/zip?zip={query.strip()},US&appid={WEATHER_API_KEY}"
        else:
            geo_url = f"http://api.openweathermap.org/geo/1.0/direct?q={query}&limit=1&appid={WEATHER_API_KEY}"

        async with session.get(geo_url) as geo_resp:
            if geo_resp.status != 200:
                return None
            
            geo_data = await geo_resp.json()
            
            if isinstance(geo_data, list):
                if not geo_data: return None
                location_info = geo_data[0]
            else:
                location_info = geo_data

            official_name = location_info['name']
            lat = location_info['lat']
            lon = location_info['lon']
            country = location_info['country']

        # --- STEP 2: WEATHER ---
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
    """Sets the user's preferred units (metric/imperial)."""
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

    # Update or Insert just the unit preference
    # We use a trick here: If user doesn't exist, we insert with null location.
    # If they do exist, we just update the units.
    cursor.execute('''
        INSERT INTO users (user_id, units) VALUES (?, ?)
        ON CONFLICT(user_id) DO UPDATE SET units=excluded.units
    ''', (user_id, new_unit))
    conn.commit()
    
    await ctx.send(f"✅ Preferences updated! I will now show you weather in **{display}**.")

@bot.command(aliases=['we', 'wea'])
async def w(ctx, *, location: str = None):
    user_id = ctx.author.id

    # 1. Fetch User Profile (Location + Units)
    cursor.execute('SELECT location, units FROM users WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    
    saved_location = result[0] if result else None
    # Default to imperial if unit is None (new user)
    user_units = result[1] if result and result[1] else 'imperial'

    # 2. Determine Search Query
    if location:
        # Save new location, keep existing unit preference
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

    # 3. Fetch Data
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
        
        # Determine Labels based on Unit System
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
