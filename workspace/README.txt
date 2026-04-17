workspace/

Scratch workspace for running pdf2md.py against real PDFs. Drop
a PDF here and run:

    ./pdf2md.py workspace/<name>.pdf workspace/<name>.md

Everything in this directory is gitignored except this file —
transcribed .md files and source PDFs stay local. Named README.txt
(not .md) so `rm *.md *.pdf` in this folder leaves it alone.

See ../workspace-transcription-protocol.md for the full per-PDF
procedure including the vision-fallback path.
