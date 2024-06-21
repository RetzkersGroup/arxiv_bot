import datetime
from typing import List, Tuple
import re
from dataclasses import dataclass, field

# import arxiv
# import discord
# from discord.ext import commands, tasks
# from discord import Message
import requests
import sys
import json
import asyncio
import feedparser
from pathlib import Path


# Change this to the command you want to use to trigger the bot
TRIGGER_COMMAND = 'papers'

THRESHOLD_SCORE = 5
THRESHOLD_STAR = 15
DAYS_BACK = 7

PUBLISH_HOUR = 10

LAST_PUBLISHED_PATH = Path(Path(__file__).parent, 'last_published').with_suffix('.json')


@dataclass
class Criteria:
    authors: List[Tuple[str, int]] = field(default_factory=list)
    good_keywords: List[Tuple[str, int]] = field(default_factory=list)
    bad_keywords: List[Tuple[str, int]] = field(default_factory=list)


# Function to load criteria from a JSON file
def load_criteria_from_json(file_path):
    # Read the JSON data from a file
    with open(file_path, 'r') as file:
        json_data = file.read()
    
    # Deserialize the JSON string to a dictionary
    data_dict = json.loads(json_data)
    
    # Convert the dictionary back to a Criteria dataclass instance
    return Criteria(**data_dict)


""" --------------------------------------------- Paper utilities -------------------------------------------------- """


def get_quant_ph_papers(date, days_back):
    base_url = 'http://export.arxiv.org/api/query?'
    start_date = date - datetime.timedelta(days=days_back)
    date_from = start_date.strftime('%Y%m%d') + '0000'
    date_to = date.strftime('%Y%m%d') + '2359'
    query = f'search_query=cat:quant-ph+AND+submittedDate:[{date_from}+TO+{date_to}]&sortBy=submittedDate&sortOrder=ascending&max_results=1000'
    url = base_url + query
    response = feedparser.parse(url)

    if response.status == 200:
        return response.entries
    else:
        raise Exception(f'Error fetching data from arXiv API: {response.status}')


def filter_papers_by_most_recent_date(papers):
    filtered_papers = []
    most_recent_date = datetime.datetime.strptime(papers[-1].published, '%Y-%m-%dT%H:%M:%SZ').date()

    for paper in papers:
        publish_date = datetime.datetime.strptime(paper.published, '%Y-%m-%dT%H:%M:%SZ').date()

        if publish_date == most_recent_date:
            filtered_papers.append(paper)

    return filtered_papers


def get_paper_score(paper, criteria: Criteria):
    score = 0

    authors = ', '.join([author['name'] for author in paper['authors']])
    title = paper['title']
    abstract = paper['summary']

    score += calculate_score(authors, criteria.authors)
    score += calculate_score(title, criteria.good_keywords)
    score += calculate_score(title, criteria.bad_keywords, is_bad=True)
    score += calculate_score(abstract, criteria.good_keywords)
    score += calculate_score(abstract, criteria.bad_keywords, is_bad=True)

    return score


def calculate_score(text: str, keywords: List[Tuple[str, int]], is_bad: bool = False) -> int:
    score = 0
    for keyword, keyword_score in keywords:
        if re.search(r'\b' + re.escape(keyword) + r'\b', text, flags=re.IGNORECASE):
            score += -keyword_score if is_bad else keyword_score
    return score


def filter_papers_by_score(papers, criteria: Criteria, threshold_score: int):
    filtered_papers = []
    scores = []
    for paper in papers:
        score = get_paper_score(paper, criteria=criteria)
        if score > threshold_score:
            filtered_papers.append(paper)
            scores.append(score)

    return filtered_papers, scores


def filter_papers_by_paper_list(papers: List, json_file: str | Path = LAST_PUBLISHED_PATH) -> List:
    # Load existing papers from the JSON file
    existing_titles = []
    with open(json_file, 'r') as file:
        paper_data = json.load(file)
        for title, _ in paper_data:
            existing_titles.append(title)

    # Add new papers with unique titles
    new_papers = []
    for paper in papers:
        current_title = paper.title
        current_title = re.sub(r'\s+', ' ', current_title)
        if current_title not in existing_titles:
            new_papers.append(paper)

    return new_papers


def sort_papers_by_score(papers, scores):
    # Sorting papers by scores in descending order
    sorted_papers_scores = sorted(zip(papers, scores), key=lambda x: x[1], reverse=True)
    sorted_papers, sorted_scores = zip(*sorted_papers_scores)

    return sorted_papers, sorted_scores


def write_last_published(papers: List, json_file: str | Path = LAST_PUBLISHED_PATH) -> None:
    paper_data = []

    # Load existing data from the JSON file
    try:
        with open(json_file, 'r') as file:
            existing_data = [tuple(item) for item in json.load(file)]  # Convert elements to tuples
    except (FileNotFoundError, json.JSONDecodeError):
        existing_data = []

    # Process new data
    for paper in papers:
        title = paper.title
        title = re.sub(r'\s+', ' ', title)
        date_raw = datetime.datetime.strptime(paper.published, '%Y-%m-%dT%H:%M:%SZ').date()
        publish_date = date_raw.strftime("%Y-%m-%d")
        paper_tuple = (title, publish_date)
        paper_data.append(paper_tuple)

    # Combine existing and new data, ensuring uniqueness of entries
    combined_data_set = set(existing_data) | set(paper_data)
    combined_data = [list(item) for item in combined_data_set]  # Convert elements back to lists

    # Check if the data can be serialized to JSON without issues
    _ = json.dumps(combined_data)

    # Write updated data back to the JSON file
    with open(json_file, 'w') as file:
        json.dump(combined_data, file, indent=4)


def remove_entries_older_than_days(days_ago: int, json_file: str | Path = LAST_PUBLISHED_PATH) -> None:
    # Calculate the cutoff date
    today = datetime.datetime.now().date()
    cutoff_date = today - datetime.timedelta(days=days_ago)

    # Load existing data from the JSON file
    try:
        with open(json_file, 'r') as file:
            existing_data = json.load(file)
    except (FileNotFoundError, json.JSONDecodeError):
        print("Error reading the JSON file.")
        return

    # Filter out entries older than the cutoff date
    filtered_data = [entry for entry in existing_data if
                     datetime.datetime.strptime(entry[1], '%Y-%m-%d').date() >= cutoff_date]

    # Check if the data can be serialized to JSON without issues
    _ = json.dumps(filtered_data)

    # Write the filtered data back to the JSON file
    with open(json_file, 'w') as file:
        json.dump(filtered_data, file, indent=4)

def get_relevant_authors(paper, criteria: Criteria):
    relevant_authors = []
    paper_authors = ', '.join([author['name'] for author in paper['authors']])
    for author in criteria.authors:
        # print(author)
        if re.search(r'\b' + re.escape(author[0]) + r'\b', paper_authors, flags=re.IGNORECASE):
            relevant_authors.append(author[0])
    return relevant_authors

def create_message(relevant_papers, scores, current_date, threshold_star, criteria: Criteria):
    message = f"**Papers for {current_date.strftime('%a, %d %b %Y')}**:\n"
    for paper, score in zip(relevant_papers, scores):
        title = paper['title'].replace('\n', '').replace('  ', ' ')
        url = paper['id']
        if score <= threshold_star:
            message += f"- "
        elif score > threshold_star:
            message += f"- :star:"
        relevant_authors = ': ' + ','.join(get_relevant_authors(paper, criteria))
        if relevant_authors == ': ':
            relevant_authors = ''
        message += f" {title}{relevant_authors} - <{url}> (score: {score})\n"
    message += '\n'
    return message


def get_message(my_criteria: Criteria, output_file):
    print('Got into the send_papers_daily function')
    now = datetime.datetime.now()
    weekday = now.weekday()
    hour = now.hour
    # Check if it's a working day (Monday=0, Sunday=6)
    if weekday < 5:
        if True:  # hour == DISCORD_PUBLISH_HOUR:
            print(f'valid weekday ({weekday}) and hour ({hour}).')

            papers = get_quant_ph_papers(now, days_back=DAYS_BACK)

            new_papers = filter_papers_by_paper_list(papers)
            relevant_papers, scores = filter_papers_by_score(new_papers,
                                                             criteria=my_criteria, threshold_score=THRESHOLD_SCORE)

            if not len(relevant_papers) > 0:
                message = f"No relevant papers found for {now.strftime('%a, %d %b %Y')}."
                return message

            write_last_published(relevant_papers, output_file)

            sorted_relevant_papers, sorted_scores = sort_papers_by_score(relevant_papers, scores)

            message = create_message(sorted_relevant_papers, sorted_scores, now, THRESHOLD_STAR, criteria=my_criteria)
            return message

            # Delete old paper entries
            remove_entries_older_than_days(days_ago=21)

        else:
            print(f'valid weekday ({weekday}) but invalid hour ({hour}).')
    else:
        print(f'invalid weekday ({weekday}).')

def send_message(url, message: str):
    data = {
        "text": message
    }
    
    # Headers for the POST request
    headers = {
        'Content-Type': 'application/json'
    }
    
    # Sending the POST request
    response = requests.post(url, json=data, headers=headers)
    
    # Check if the request was successful
    if response.status_code == 200:
        print("Message sent successfully!")
    else:
        print(f"Failed to send message. Status code: {response.status_code}, Response: {response.text}")

# To run the script use main.py <slack url> <criteria file> <output_file>
if __name__ == "__main__":
    # Load criteria from the JSON file
    my_criteria = load_criteria_from_json(sys.argv[2])
    output_file = sys.argv[3]

    # Start the bot
    message = get_message(my_criteria, output_file)
    print(message)

    send_message(sys.argv[1], message)