import re
from pyrogram import filters
from pyrogram.types import Message

# Import your app instance
from SHUKLAMUSIC import app

def calculate_expression(text: str):
    """
    Safely parses and evaluates a mathematical expression.
    Returns the formatted result string or None if it's not a valid math problem.
    """
    calc_text = text.replace("÷", "/").replace("×", "*").replace(" ", "")
    
    # SECURITY VALIDATION:
    if not re.match(r"^[\d\.\+\-\*\/\(\)]+$", calc_text):
        return None
        
    # Ensure there is at least one operator.
    if not re.search(r"[\+\-\*\/]", calc_text):
        return None
        
    try:
        result = eval(calc_text, {"__builtins__": {}}, {})
        
        if isinstance(result, float) and result.is_integer():
            result = int(result)
        elif isinstance(result, float):
            result = round(result, 4) 
            
        return f"{text.strip()} = {result}"
        
    except ZeroDivisionError:
        return f"{text.strip()} = ∞ (Division by Zero)"
    except Exception:
        return None


# --- CUSTOM FILTER ---
async def math_check(_, __, message: Message):
    """Filter to check if a message is purely a math expression."""
    if not message.text:
        return False
        
    text = message.text.strip()
    
    # 1. Ignore messages that are too long
    # 2. Ignore messages that start with standard bot command prefixes
    if len(text) > 60 or text.startswith(("/", "!", ".")):
        return False
        
    # Test if the string matches our math regex
    calc_text = text.replace("÷", "/").replace("×", "*").replace(" ", "")
    if not re.match(r"^[\d\.\+\-\*\/\(\)]+$", calc_text) or not re.search(r"[\+\-\*\/]", calc_text):
        return False
        
    return True

# Initialize the custom filter
is_math_expression = filters.create(math_check)


# --- COMMAND: /calc ---
@app.on_message(filters.command("calc"))
async def calc_command(client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text("ℹ️ **Usage:** `/calc 9+9`\nSupports standard operators and ×, ÷")
        
    expression = " ".join(message.command[1:])
    result_text = calculate_expression(expression)
    
    if result_text:
        await message.reply_text(f"**{result_text}**")
    else:
        await message.reply_text("❌ **Invalid mathematical expression.**\nPlease use numbers and basic operators (+, -, *, /, ÷, ×).")


# --- EVENT LOGIC: DIRECT CALCULATION (NO COMMAND) ---
# Now using the custom filter instead of the broad 'filters.text'
@app.on_message(is_math_expression & ~filters.bot)
async def direct_calc(client, message: Message):
    result_text = calculate_expression(message.text.strip())
    
    if result_text:
        await message.reply_text(f"**{result_text}**")
