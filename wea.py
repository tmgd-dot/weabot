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

# Create table with 'units' column default to imperial (Fahrenheit)
cursor.execute('''
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        location TEXT,
        units TEXT DEFAULT 'imperial'
    )
''')
conn.commit()

# Migration: Check if 'units' column exists, add it if not
try:
    cursor.execute("SELECT units FROM users LIMIT 1")
except sqlite3.OperationalError:
    print("Migrating database: Adding 'units' column...")
    cursor.execute("ALTER TABLE users ADD COLUMN units TEXT DEFAULT 'imperial'")
    conn.commit()

# --- Bot Setup ---
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='.', intents=intents)

async def get_weather_data(query, units):
    async with aiohttp.ClientSession() as session:
        
        lat, lon = None, None
        official_name = None
        country = "US"

        # --- PATH A: US ZIP CODE LOOKUP (Zippopotam) ---
        # We use this to get the "Postal City" name (e.g. Highlands Ranch)
        # instead of the County name (Douglas County).
        if query.replace(" ", "").isdigit() and len(query.strip()) == 5:
            try:
                zip_url = f"http://api.zippopotam.us/us/{query.strip()}"
                async with session.get(zip_url) as zip_resp:
                    if zip_resp.status == 200:
                        zip_data = await zip_resp.json()
                        place = zip_data['places'][0]
                        official_name = place['place name']
                        lat = place['latitude']
                        lon = place['longitude']
                        country = "US"
            except Exception as e:
                print(f"Zippopotam lookup failed: {e}")

        # --- PATH B: OPENWEATHERMAP FALLBACK ---
        # If Path A failed or it's not a zip, use standard OWM Geocoding
        if not lat or not lon:
            geo_url = f"http://api.openweathermap.org/geo/1.0/direct?q={query}&limit=1&appid={WEATHER_API_KEY}"
            async with session.get(geo_url) as geo_resp:
                if geo_resp.status != 200: return None
                geo_data = await geo_resp.json()
                
                if not geo_data: return None
                location_info = geo_data[0]
                
                lat = location_info['lat']
                lon = location_info['lon']
                official_name = location_info['name']
                country = location_info.get('country', 'US')

        # --- STEP 3: FETCH WEATHER ---
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

    cursor.execute('''
        INSERT INTO users (user_id, units) VALUES (?, ?)
        ON CONFLICT(user_id) DO UPDATE SET units=excluded.units
    ''', (user_id, new_unit))
    conn.commit()
    await ctx.send(f"✅ Preferences updated! I will now show you weather in **{display}**.")

@bot.command(aliases=['wx', 'we', 'wea']) 
async def weather(ctx, *, location: str = None):
    # Default target is the author
    target_user_id = ctx.author.id
    search_query = None
    
    # 1. Check Input (Is it a Location or a User Mention?)
    if location:
        try:
            # Try to convert input to a User (e.g. .wx @Friend)
            converter = commands.MemberConverter()
            target_member = await converter.convert(ctx, location)
            
            # If successful, we are looking up THAT user
            target_user_id = target_member.id
            
            # Read-Only lookup
            cursor.execute('SELECT location FROM users WHERE user_id = ?', (target_user_id,))
            result = cursor.fetchone()
            
            if result:
                search_query = result[0]
            else:
                await ctx.send(f"❌ **{target_member.display_name}** hasn't set their location yet!")
                return
                
        except commands.BadArgument:
            # Input is NOT a user, so it's a City/Zip.
            # Save this to the AUTHOR'S profile.
            
            # Fetch existing units to preserve them
            cursor.execute('SELECT units FROM users WHERE user_id = ?', (ctx.author.id,))
            res = cursor.fetchone()
            user_units = res[0] if res and res[0] else 'imperial'
            
            cursor.execute('''
                INSERT INTO users (user_id, location, units) VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET location=excluded.location
            ''', (ctx.author.id, location, user_units))
            conn.commit()
            
            search_query = location

    else:
        # No input, check Author's saved location
        cursor.execute('SELECT location FROM users WHERE user_id = ?', (target_user_id,))
        result = cursor.fetchone()
        if result:
            search_query = result[0]
        else:
            await ctx.send("❌ **No location found!**\nPlease type `.wx <ZipCode>` or `.wx <City>`.")
            return

    # 2. Get Units for the TARGET user
    cursor.execute('SELECT units FROM users WHERE user_id = ?', (target_user_id,))
    res = cursor.fetchone()
    units_to_use = res[0] if res and res[0] else 'imperial'

    # 3. Fetch Data
    data = await get_weather_data(search_query, units_to_use)
    
    if data:
        city = data['name']
        country = data['sys']['country']
        temp = data['main']['temp']
        feels_like = data['main']['feels_like']
        humidity = data['main']['humidity']
        wind_speed = data['wind']['speed']
        condition = data['weather'][0]['description'].capitalize()
        icon_code = data['weather'][0]['icon']
        
        # Set labels
        if units_to_use == 'imperial':
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
        
        # --- THE ICON UPGRADE ---
        # We use the @4x URL to get a large, crisp PNG image.
        icon_url = f"http://openweathermap.org/img/wn/{icon_code}@4x.png"
        embed.set_thumbnail(url=icon_url)

        embed.add_field(name="Temperature", value=f"{round(temp, 1)}{temp_label}", inline=True)
        embed.add_field(name="Feels Like", value=f"{round(feels_like, 1)}{temp_label}", inline=True)
        embed.add_field(name="Humidity", value=f"{humidity}%", inline=True)
        embed.add_field(name="Wind Speed", value=f"{wind_speed} {speed_label}", inline=True)
        
        # Smart Footer
        req_text = f"Requested by {ctx.author.display_name}"
        if target_user_id != ctx.author.id:
            try:
                target_user = await bot.fetch_user(target_user_id)
                req_text += f" • For {target_user.display_name}"
            except:
                pass
        embed.set_footer(text=req_text)

        await ctx.send(embed=embed)
    else:
        await ctx.send(f"⚠️ Could not find location **'{search_query}'**.")

if __name__ == "__main__":
    bot.run(TOKEN)
