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
    """Fetches weather data from OpenWeatherMap."""
    # Requesting metric by default
    url = f"http://api.openweathermap.org/data/2.5/weather?q={query}&appid={WEATHER_API_KEY}&units=metric"
    
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            if response.status == 200:
                return await response.json()
            return None

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user}')
    # Sets a status message like "Playing with Thermostats"
    await bot.change_presence(activity=discord.Game(name="the Weather"))

@bot.command(aliases=['we', 'wea'])
async def w(ctx, *, location: str = None):
    user_id = ctx.author.id

    # 1. Handle Location Logic
    if location:
        cursor.execute('INSERT OR REPLACE INTO users (user_id, location) VALUES (?, ?)', (user_id, location))
        conn.commit()
        search_query = location
        # React to the message to acknowledge the save without cluttering chat
        await ctx.message.add_reaction("💾") 
    else:
        cursor.execute('SELECT location FROM users WHERE user_id = ?', (user_id,))
        result = cursor.fetchone()
        
        if result:
            search_query = result[0]
        else:
            await ctx.send("❌ **No location found!**\nPlease type `.w <City, Country>` (e.g., `.w Vancouver, CA`) to set it.")
            return

    # 2. Fetch Data
    data = await get_weather_data(search_query)
    
    if data:
        # Extract variables
        city = data['name']
        country = data['sys']['country']
        temp = data['main']['temp']
        feels_like = data['main']['feels_like']
        humidity = data['main']['humidity']
        wind_speed = data['wind']['speed'] # m/s because we used metric
        condition = data['weather'][0]['description'].capitalize()
        icon_code = data['weather'][0]['icon']
        
        # 3. Create the Embed
        embed = discord.Embed(
            title=f"Weather in {city}, {country}",
            description=f"**{condition}**",
            color=0x3498db # A nice calm blue
        )
        
        # Add the dynamic weather icon
        icon_url = f"http://openweathermap.org/img/wn/{icon_code}@2x.png"
        embed.set_thumbnail(url=icon_url)

        # Add data fields
        embed.add_field(name="Temperature", value=f"{round(temp, 1)}°C", inline=True)
        embed.add_field(name="Feels Like", value=f"{round(feels_like, 1)}°C", inline=True)
        embed.add_field(name="Humidity", value=f"{humidity}%", inline=True)
        embed.add_field(name="Wind Speed", value=f"{wind_speed} m/s", inline=True)
        
        # Add footer
        embed.set_footer(text=f"Requested by {ctx.author.display_name}")

        await ctx.send(embed=embed)
    else:
        await ctx.send(f"⚠️ Could not find weather for **'{search_query}'**. Please check the spelling.")

if __name__ == "__main__":
    bot.run(TOKEN)