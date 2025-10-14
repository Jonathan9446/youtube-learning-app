# Utility functions for YouTube Learning App

def format_duration(seconds: int) -> str:
    """Convert seconds to HH:MM:SS format"""
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    seconds = seconds % 60
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

def clean_text(text: str) -> str:
    """Clean and normalize text"""
    import re
    text = re.sub(r'\s+', ' ', text)
    return text.strip()
