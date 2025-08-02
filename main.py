
from fastapi import FastAPI, Request, Form
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
import pandas as pd
from sklearn.preprocessing import MinMaxScaler
from fuzzywuzzy import process, fuzz
import ast
from typing import Optional

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# Custom Jinja2 filter to format floats to one decimal place
def floatformat(value, precision=1):
    try:
        return f"{float(value):.{precision}f}"
    except (ValueError, TypeError):
        return value

# Add custom filter to Jinja2 environment
templates.env.filters['floatformat'] = floatformat

# Define functions
def safe_literal_eval(val):
    try:
        return ast.literal_eval(val)
    except:
        return val

def flatten_attributes(attr_dict):
    flat_dict = {}
    for key, value in attr_dict.items():
        if isinstance(value, str):
            nested = safe_literal_eval(value)
            if isinstance(nested, dict):
                for sub_key, sub_val in nested.items():
                    flat_dict[f"{key}_{sub_key}"] = sub_val
            else:
                flat_dict[key] = nested
        else:
            flat_dict[key] = value
    return flat_dict

def parse_simple_keywords(input_str):
    return [kw.strip().lower() for kw in input_str.split(',') if kw.strip()]

def filter_by_true_keywords(df, keywords):
    def matches(attr_dict):
        if not isinstance(attr_dict, dict):
            return False
        for kw in keywords:
            found = False
            for key, value in attr_dict.items():
                if kw in key.lower() and value is True:
                    found = True
                    break
            if not found:
                return False
        return True
    return df[df['attributes'].apply(matches)]

def recommend_restaurants(stars, review_count, categories, city, attributes, restaurants):
    # Validate inputs
    categories = categories or 'Restaurants'
    stars = 3.0 if stars < 1.0 or stars > 5.0 else stars
    review_count = 0 if review_count < 0 else review_count
    attributes = attributes or 'GoodForKids,BusinessAcceptsCreditCards,OutdoorSeating'

    # Check if categories might be a city
    cities = restaurants['city'].str.lower().unique()
    is_city_like, score = process.extractOne(categories.lower(), cities, scorer=fuzz.token_sort_ratio)
    city_warning = f"Warning: '{categories}' appears to be a city, not a category. Consider specifying a category (e.g., Sushi Bars, Italian, Dim Sum)." if is_city_like and score >= 75 else ""

    # Filter by city
    filtered_df = restaurants.copy()
    matched_city = city
    if city:
        best_city, score = process.extractOne(city.lower(), cities, scorer=fuzz.token_sort_ratio)
        if score >= 75:
            filtered_df = filtered_df[filtered_df['city'].str.lower() == best_city]
            matched_city = best_city
        else:
            filtered_df = pd.DataFrame(columns=filtered_df.columns)
            matched_city = None

    # Fuzzy match categories
    if not filtered_df.empty:
        all_cats = filtered_df['categories'].str.split(', ').explode().str.strip().unique()
        best_cats = process.extractBests(categories, all_cats, scorer=fuzz.token_sort_ratio, score_cutoff=65, limit=3)
        cat_pattern = '|'.join([cat[0] for cat in best_cats]) if best_cats else 'Restaurants'
        filtered_df = filtered_df[filtered_df['categories'].str.contains(cat_pattern, case=False, na=False)]

    # Filter stars and review_count
    if not filtered_df.empty:
        filtered_df_strict = filtered_df[(filtered_df['stars'] >= stars) & (filtered_df['review_count'] >= review_count)]
        filtered_df = filtered_df_strict if not filtered_df_strict.empty else filtered_df[(filtered_df['stars'] >= stars * 0.8) & (filtered_df['review_count'] >= review_count * 0.5)]

    # Filter by attribute keywords
    if attributes and not filtered_df.empty:
        keywords = parse_simple_keywords(attributes)
        filtered_df = filter_by_true_keywords(filtered_df, keywords)

    # Get recommendations
    recommendations = filtered_df.sort_values('score', ascending=False).head(5)[['name', 'address', 'city', 'stars', 'review_count', 'categories']]

    # Fallback if no matches
    message = ""
    if recommendations.empty:
        message = f"No restaurants found for '{categories}' in '{city or 'Any City'}' with {stars:.1f}+ stars, {review_count}+ reviews, and '{attributes}'. Showing top restaurants."
        recommendations = restaurants.sort_values('score', ascending=False).head(5)[['name', 'address', 'city', 'stars', 'review_count', 'categories']]
    elif matched_city != city and city:
        message = f"No restaurants in '{city}', but found matches in '{matched_city}' for '{categories}'."
    elif not best_cats:
        message = f"No close match for category '{categories}', showing restaurants with similar categories."

    return recommendations, message, city_warning

# Load and preprocess data at startup
df = pd.read_csv("data.csv")
df['attributes'] = df['attributes'].apply(safe_literal_eval)
df['attributes'] = df['attributes'].apply(flatten_attributes)
restaurants = df[(df["is_open"]==1) & (df["categories"].str.contains("Restaurants",case=False,na=False))].copy()
scaler = MinMaxScaler()
restaurants['stars_norm'] = scaler.fit_transform(restaurants[['stars']])
restaurants['review_count_norm'] = scaler.fit_transform(restaurants[['review_count']])
restaurants['score'] = 0.6 * restaurants['stars_norm'] + 0.4 * restaurants['review_count_norm']

@app.get("/")
async def root(request: Request):
    return templates.TemplateResponse("index.html", {
        "request": request,
        "recommendations": [],
        "message": "",
        "city_warning": ""
    })

@app.post("/recommend")
async def recommend(
    request: Request,
    stars: float = Form(...),
    review_count: int = Form(...),
    categories: str = Form(...),
    city: Optional[str] = Form(None),
    attributes: Optional[str] = Form(None)
):
    recommendations, message, city_warning = recommend_restaurants(stars, review_count, categories, city, attributes, restaurants)
    recommendations = recommendations.to_dict('records')

    return templates.TemplateResponse("index.html", {
        "request": request,
        "recommendations": recommendations,
        "message": message,
        "city_warning": city_warning,
        "stars": stars,
        "review_count": review_count,
        "categories": categories,
        "city": city,
        "attributes": attributes
    })

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
