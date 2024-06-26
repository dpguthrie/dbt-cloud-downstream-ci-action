# Use an official Python runtime as a parent image
FROM python:3.9.18-slim

# Set the working directory in the container
ADD . /app
WORKDIR /app

# Copy the dependencies file to the working directory
COPY /requirements ./requirements

# Install any dependencies
ENV VIRTUAL_ENV=/usr/local
RUN pip install uv && uv pip install --system --no-cache -r requirements/prod.txt

# Copy the script to the container
COPY src/main.py .

# Run the script when the container launches
CMD ["python", "/app/main.py"]
