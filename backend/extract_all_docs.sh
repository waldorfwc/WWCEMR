#!/bin/bash
# Extract all 46 tar parts to OWC External drive
# Parts are numbered in reverse: 046 is the start, 001 is the end

SRC="/Volumes/OWC External/Documents"
DEST="/Volumes/OWC External/ExtractedDocuments"

echo "Extracting 46 tar parts (~230GB) to $DEST"
echo "Started: $(date)"

# Build the cat command with parts in reverse order (046 down to 001)
PARTS=""
for i in $(seq -w 46 -1 1); do
    P="$SRC/Document.tar-0${i}"
    [ ! -f "$P" ] && P="$SRC/Document.tar-${i}"
    if [ -f "$P" ]; then
        PARTS="$PARTS \"$P\""
    fi
done

eval cat $PARTS | tar xf - -C "$DEST" 2>&1

echo ""
echo "Finished: $(date)"
echo "Files extracted:"
find "$DEST" -type f | wc -l
