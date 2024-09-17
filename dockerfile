# Base image with Python 3
FROM python:3.9

# Update package lists (optional, but recommended)
RUN apt-get update

# Install software-properties-common (required for add-apt-repository)
RUN apt-get install -y software-properties-common

# Add non-free repositories
RUN add-apt-repository -y non-free

# Install system dependencies for Flask
RUN apt-get install -y build-essential libssl-dev libffi-dev python3-dev libmediainfo0v5

# Create a working directory
WORKDIR /app

# Install dependencies (assuming you have a requirements.txt)
COPY requirements.txt .
RUN pip install -r requirements.txt
RUN pip3 install ffmpeg-progress-yield

# Prepare Jellyfin repository (using environment variables)
RUN \
    sed -i "s/Components: main/Components: main non-free/" /etc/apt/sources.list.d/debian.sources \
    && mkdir -p /etc/apt/keyrings \
    && curl -fsSL https://repo.jellyfin.org/jellyfin_team.gpg.key | gpg --dearmor -o /etc/apt/keyrings/jellyfin.gpg \
    && VERSION_OS="$( awk -F'=' '/^ID=/{ print $NF }' /etc/os-release )" \
    && VERSION_CODENAME="$( awk -F'=' '/^VERSION_CODENAME=/{ print $NF }' /etc/os-release )" \
    && DPKG_ARCHITECTURE="$( dpkg --print-architecture )" \
    && echo "Types: deb" > /etc/apt/sources.list.d/jellyfin.sources \
    && echo "URIs: https://repo.jellyfin.org/${VERSION_OS}" >> /etc/apt/sources.list.d/jellyfin.sources \
    && echo "Suites: ${VERSION_CODENAME}" >> /etc/apt/sources.list.d/jellyfin.sources \
    && echo "Components: main" >> /etc/apt/sources.list.d/jellyfin.sources \
    && echo "Architectures: ${DPKG_ARCHITECTURE}" >> /etc/apt/sources.list.d/jellyfin.sources \
    && echo "Signed-By: /etc/apt/keyrings/jellyfin.gpg" >> /etc/apt/sources.list.d/jellyfin.sources \
    && apt-get update \
    && apt-get install -y jellyfin-ffmpeg6

# Install gpu dependencies
RUN apt install -y onevpl-tools vainfo intel-media-va-driver-non-free

# Install nano to help with troubleshooting and testing
RUN apt install -y nano

# Copy Python script and webpage files
COPY . .

# Expose port for the Flask app
EXPOSE 5000

# Change permissions on config directory
RUN mkdir -p /config && chmod 777 /config

# Run the Flask app (replace 'yourapp' with your script name)
CMD ["python", "flaskapp.py"]
