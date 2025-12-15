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

# Schema migration
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
        
        lat, lon = None, None
        official_name = None
        country = "US"

        # --- PATH A: US ZIP CODE LOOKUP (Zippopotam) ---
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

@bot.command(aliases=['wx', 'we', 'wea']) 
async def weather(ctx, *, location: str = None):
    # Default to the author (the person typing the command)
    target_user_id = ctx.author.id
    search_query = None
    
    # 1. Determine if the user provided an input
    if location:
        # 2. Check if the input is a Mention (e.g., <@123456789>)
        try:
            # Attempt to convert the string input into a Member object
            converter = commands.MemberConverter()
            target_member = await converter.convert(ctx, location)
            
            # --- IT IS A USER LOOKUP ---
            target_user_id = target_member.id
            
            # Retrieve that target user's location (READ ONLY)
            cursor.execute('SELECT location FROM users WHERE user_id = ?', (target_user_id,))
            result = cursor.fetchone()
            
            if result:
                search_query = result[0]
            else:
                await ctx.send(f"❌ **{target_member.display_name}** hasn't set their location yet!")
                return
                
        except commands.BadArgument:
            # --- IT IS A NEW LOCATION SAVE ---
            # The input was NOT a user, so it must be a city/zip.
            # We save this to the AUTHOR'S profile (overwriting their old location)
            
            # First, get their existing unit preference so we don't lose it
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
        # No input provided, just look up the author's own saved location
        cursor.execute('SELECT location FROM users WHERE user_id = ?', (target_user_id,))
        result = cursor.fetchone()
        if result:
            search_query = result[0]
        else:
            await ctx.send("❌ **No location found!**\nPlease type `.wx <ZipCode>` or `.wx <City>`.")
            return

    # 3. Fetch Units for the TARGET user (so we see it in their preferred units)
    #    (Or you could change this to ctx.author.id if you always want to see YOUR units)
    cursor.execute('SELECT units FROM users WHERE user_id = ?', (target_user_id,))
    res = cursor.fetchone()
    units_to_use = res[0] if res and res[0] else 'imperial'

    # 4. Fetch Data
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
        embed.set_thumbnail(url=f"http://openweathermap.org/img/wn/{icon_code}@2x.png")
        embed.add_field(name="Temperature", value=f"{round(temp, 1)}{temp_label}", inline=True)
        embed.add_field(name="Feels Like", value=f"{round(feels_like, 1)}{temp_label}", inline=True)
        embed.add_field(name="Humidity", value=f"{humidity}%", inline=True)
        embed.add_field(name="Wind Speed", value=f"{wind_speed} {speed_label}", inline=True)
        
        # Footer now shows who requested it vs whose weather it is
        req_text = f"Requested by {ctx.author.display_name}"
        if target_user_id != ctx.author.id:
            # If we looked up someone else, mention that in the footer
            # We need to fetch the member object again if we don't have it handy
            # or just generic text. Since we have target_user_id, we can try:
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
