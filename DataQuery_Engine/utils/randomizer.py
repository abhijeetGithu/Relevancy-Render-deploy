import random

def generate_random_numbers(max_page: int, iterations: int, min_page: int = 1):
    """Generate a list of random page numbers based on max_page, iterations, and optional min_page."""
    return [random.randint(min_page, max_page) for _ in range(iterations)]