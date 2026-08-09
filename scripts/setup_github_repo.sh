#!/usr/bin/env bash
# Apply the repository's public metadata: description, homepage and topics.
#
# GitHub topics are the main way people discover a repository through search and through the
# topic pages themselves, and they are not stored in the repository — they live on the GitHub
# side and can only be set through the API or the web UI. That makes them the one piece of the
# project that cannot be version-controlled, which is exactly why it is scripted here rather than
# left as a note somebody has to remember.
#
# Requires the GitHub CLI, authenticated with a token that can administer the repository:
#
#     gh auth login
#     ./scripts/setup_github_repo.sh
#
# Override the target with REPO=owner/name.

set -euo pipefail

REPO="${REPO:-teocreate/AI-Musical-Proofreader}"

DESCRIPTION="A spell checker for sheet music — finds and explains the notes your OMR engine got wrong, in the MusicXML it just produced."

# Ordered roughly by how someone would actually search. The first few are the terms a musician
# with a bad OMR result types; the rest are the terms a developer browsing the ecosystem types.
TOPICS=(
  optical-music-recognition
  omr
  sheet-music
  music-notation
  musicxml
  musescore
  music-theory
  score-analysis
  proofreading
  error-detection
  music-information-retrieval
  digital-humanities
  python
  pyside6
  computer-vision
  music21
)

echo "Repository: $REPO"

gh repo edit "$REPO" \
  --description "$DESCRIPTION" \
  --homepage "https://github.com/$REPO"

# gh repo edit --add-topic takes one at a time; the API endpoint replaces the whole set at once,
# which is what we want so that removing a topic from the list above actually removes it.
printf -v topic_json '"%s",' "${TOPICS[@]}"
gh api -X PUT "repos/$REPO/topics" \
  -H "Accept: application/vnd.github+json" \
  --input - <<JSON
{"names": [${topic_json%,}]}
JSON

echo
echo "Applied ${#TOPICS[@]} topics."
echo "Check: https://github.com/$REPO"
