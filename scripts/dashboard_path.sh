# Shared PATH for App launches. Finder apps and some Windows shells
# do not include Docker's install location by default.
if ! command -v docker >/dev/null 2>&1; then
  export PATH="/usr/local/bin:/opt/homebrew/bin:${HOME}/.docker/bin:/Applications/Docker.app/Contents/Resources/bin:/c/Program Files/Docker/Docker/resources/bin:${PATH:-/usr/bin:/bin}"
fi
