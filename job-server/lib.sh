#!/bin/bash

# make sure a value is set in a .env file
ensure_value() {
    local name="$1"
    local value="$2"
    local file="$3"

    echo "Setting $name=$value in $file"

    # set naked value
    if grep -q "^$name=" "$file" 2>/dev/null; then
        # use '|' sed delimiter as we use '/' in values
        sed -i "s|^$name=.*|$name=\"$value\"|" "$file"
    # set and uncomment commented line
    elif grep -q "^#$name=" "$file" 2>/dev/null; then
        sed -i "s|^#$name=.*|$name=\"$value\"|" "$file"
    # append the line as it does not exist
    else
        echo "$name=\"$value\"" >> "$file"
    fi
}
