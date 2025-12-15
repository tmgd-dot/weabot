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
        location TEXT
    )
''')
conn.commit()

# --- Bot Setup ---
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='.', intents=intents)

async def get_weather_data(query):
    """
    Two-step process:
    1. Geocode the query to get coordinates and a 'pretty' name.
    2. Fetch weather using those coordinates.
    """
    
    # Session is shared for both requests
    async with aiohttp.ClientSession() as session:
        
        # --- STEP 1: GEOCODING ---
        # Determine if we are searching by Zip Code or City Name
        if query.replace(" ", "").isdigit() and len(query.strip()) == 5:
            # It's a US Zip Code (e.g., "80129")
            geo_url = f"http://api.openweathermap.org/geo/1.0/zip?zip={query.strip()},US&appid={WEATHER_API_KEY}"
        else:
            # It's a City Name (e.g., "Paris, FR" or "Littleton, CO")
            # We use 'direct' geocoding for cities. Limit=1 means "give me the best match".
            geo_url = f"http://api.openweathermap.org/geo/1.0/direct?q={query}&limit=1&appid={WEATHER_API_KEY}"

        # Fetch the location details
        async with session.get(geo_url) as geo_resp:
            if geo_resp.status != 200:
                return None
            
            geo_data = await geo_resp.json()
            
            # Handle different response formats (Zip returns dict, Direct returns list)
            if isinstance(geo_data, list):
                if not geo_data: return None # No results
                location_info = geo_data[0]
            else:
                location_info = geo_data

            # Extract the "Official" data
            official_name = location_info['name']
            lat = location_info['lat']
            lon = location_info['lon']
            country = location_info['country']

        # --- STEP 2: WEATHER ---
        # Now we ask for weather at this exact lat/lon
        weather_url = f"http://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&appid={WEATHER_API_KEY}&units=metric"
        
        async with session.get(weather_url) as weather_resp:
            if weather_resp.status == 200:
                weather_data = await weather_resp.json()
                # Inject the "Official Name" back into the weather data so the display is correct
                weather_data['name'] = official_name
                weather_data['sys']['country'] = country
                return weather_data
            return None

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user}')
    await bot.change_presence(activity=discord.Game(name="the Weather"))

@bot.command(aliases=['we', 'wea'])
async def w(ctx, *, location: str = None):
    user_id = ctx.author.id

    if location:
        cursor.execute('INSERT OR REPLACE INTO users (user_id, location) VALUES (?, ?)', (user_id, location))
        conn.commit()
        search_query = location
    else:
        cursor.execute('SELECT location FROM users WHERE user_id = ?', (user_id,))
        result = cursor.fetchone()
        if result:
            search_query = result[0]
        else:
            await ctx.send("❌ **No location found!**\nPlease type `.w <ZipCode>` or `.w <City>`.")
            return

    data = await get_weather_data(search_query)
    
    if data:
        city = data['name']
        country = data['sys']['country']
        temp = data['main']['temp']
        feels_like = data['main']['feels_like']
        humidity = data['main']['humidity']
        wind_speed = data['wind']['speed']
        condition = data['weather'][0]['description'].capitalize()
        icon_code = data['weather'][0]['icon']
        
        embed = discord.Embed(
            title=f"Weather in {city}, {country}",
            description=f"**{condition}**",
            color=0x3498db
        )
        embed.set_thumbnail(url=f"http://openweathermap.org/img/wn/{icon_code}@2x.png")
        embed.add_field(name="Temperature", value=f"{round(temp, 1)}°C", inline=True)
        embed.add_field(name="Feels Like", value=f"{round(feels_like, 1)}°C", inline=True)
        embed.add_field(name="Humidity", value=f"{humidity}%", inline=True)
        embed.add_field(name="Wind Speed", value=f"{wind_speed} m/s", inline=True)
        embed.set_footer(text=f"Requested by {ctx.author.display_name}")

        await ctx.send(embed=embed)
    else:
        await ctx.send(f"⚠️ Could not find location **'{search_query}'**.")

if __name__ == "__main__":
    bot.run(TOKEN)
