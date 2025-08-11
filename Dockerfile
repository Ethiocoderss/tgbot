# Use an official Python image
FROM python:3.11-slim

# Install FFmpeg (this will work inside the Docker environment)
RUN apt-get update && apt-get install -y ffmpeg

# Set the working directory for your bot
WORKDIR /app

# Copy your requirements file
COPY requirements.txt .

# Install the Python libraries
RUN pip install -r requirements.txt

# Copy your bot's script
COPY bot.py .

# This command will be run to start your bot
CMD ["python", "bot.py"]
