import requests
import os
from dotenv import load_dotenv

load_dotenv()

# Your CapSolver API key
api_key = os.getenv("API_KEY")


def solve_image(base64_image):
    """
    Solve a base64 encoded image using CapSolver
    Args:
        base64_image: Base64 encoded image string
    Returns:
        Recognition result
    """
    payload = {
        "clientKey": api_key,
        "task": {
            "type": "ImageToTextTask",
            "module": "module_016",  # Use number module for number recognition
            "body": base64_image,  # Single image
        },
    }
    
    response = requests.post("https://api.capsolver.com/createTask", json=payload)
    result = response.json()
    
    if result.get("errorId") == 0 and "solution" in result:
        solution = result["solution"]
        if "text" in solution:
            return solution["text"]
        elif "answers" in solution:
            return solution["answers"]
    else:
        print(f"Error: {result}")
        return None


def main():
    # Read base64 image from text file
    try:
        with open("image.txt", "r") as f:
            base64_image = f.read().strip()
    except FileNotFoundError:
        print("Error: image.txt file not found in the current directory")
        return
    
    if not base64_image:
        print("Error: image.txt is empty")
        return
    
    print("Solving image with CapSolver...")
    result = solve_image(base64_image)
    
    if result:
        print(f"Result: {result}")
    else:
        print("Failed to solve image")


if __name__ == "__main__":
    main()
