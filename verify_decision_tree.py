"""
Verifies that all generated decision trees are valid
"""

import json
import logging
import pickle

import numpy as np
import pandas as pd

from parse_data import read_all_answers
from play import eval_guess
from possibilities_table import array_to_integer, guess_response_to_string


def find_answer_in_tree(
    answer: str,
    tree: dict,
    depth: int,
    guess_words: list[str],
    answer_words: list[str],
    guesses: list[str] | None = None,
) -> tuple[int, list[str]]:
    """
    Find the given answer in the provided tree
    Return the depth
    Throw an error if it is not found
    """

    if guesses is None:
        guesses = []

    # get the top-level key
    root_words = list(tree.keys())
    assert len(root_words) == 1
    root_word = root_words[0]  # type: int
    guess_word = guess_words[int(root_word)]
    logging.info("depth %d, guessing %s", depth, guess_word)
    guesses.append(guess_word)

    rv = eval_guess(guess_word, answer)
    rvi = array_to_integer(rv)
    rvs = guess_response_to_string(rvi)
    logging.info("Response: %s", rvs)

    if rvi == 242:
        logging.info("Correct!")
        return depth, guesses

    action_map = tree[root_word]
    if str(rvi) in action_map:
        return find_answer_in_tree(
            answer=answer,
            tree=action_map[str(rvi)],
            depth=depth + 1,
            guess_words=guess_words,
            answer_words=answer_words,
            guesses=guesses,
        )
    else:
        raise Exception(
            "Failed to find answer %s. No action to perform here: %s. Path is %s"
            % (answer, rvs, str(guesses))
        )


def check_file(path: str, dictionary: str) -> pd.DataFrame:
    tree = {}
    root_word = path.split("/")[-1].split(".")[0]
    with open(path) as fp:
        tree = json.load(fp)

    guess_words = []
    answer_words = []

    guess_words, answer_words = get_words_for_dictionary(dictionary)

    d = {}
    for answer in answer_words:
        depth, _ = find_answer_in_tree(
            answer=answer,
            tree=tree,
            depth=1,
            guess_words=guess_words,
            answer_words=answer_words,
        )
        d[answer] = depth

    df = pd.DataFrame({"answer": d.keys(), "depth": d.values()})
    # print(df)
    mean_depth = np.mean(df["depth"])
    print(f"mean depth for tree rooted at {root_word} is {mean_depth:.2f}")
    return df


def convert_tree_to_human_readable(
    tree: dict, guess_words: list[str], answer_words: list[str]
) -> dict:
    """
    Convert a decision tree to one that is human-readable
    """
    hr_tree = {}
    hr_action_map = {}

    root_words = list(tree.keys())
    assert len(root_words) == 1
    root_word = root_words[0]  # type: int
    guess_word = guess_words[int(root_word)]
    hr_tree[guess_word] = hr_action_map

    for rvi in tree[root_word]:
        rvi = int(rvi)
        rvs = guess_response_to_string(rvi)
        hr_action_map[rvs] = convert_tree_to_human_readable(
            tree=tree[root_word][str(rvi)],
            guess_words=guess_words,
            answer_words=answer_words,
        )

    return hr_tree


def convert_file_to_human_readable(in_path: str, dictionary: str) -> str:
    """Returns the output path"""
    tree = {}
    root_word = in_path.split("/")[-1].split(".")[0]
    print(f"Converting tree {in_path} to human readable format...")
    with open(in_path) as fp:
        tree = json.load(fp)

    guess_words, answer_words = get_words_for_dictionary(dictionary)

    out_tree = convert_tree_to_human_readable(tree, guess_words, answer_words)
    print("Converted.")
    out_path = f"./out/decision-trees/{dictionary}/{root_word}-hr.json"
    with open(out_path, "w") as fp:
        json.dump(out_tree, fp, sort_keys=True, indent=4)
    print(f"Wrote to {out_path}")
    return out_path


def get_dictionary_from_filename(path: str):
    # it's the second to last item
    return path.split("/")[-2]


def get_words_for_dictionary(dictionary: str) -> tuple[list[str], list[str]]:
    if dictionary == "answers":
        guess_words = read_all_answers()
        answer_words = read_all_answers()
        return guess_words, answer_words
    elif dictionary == "asymmetric":
        # actually need to use another set of files
        with open("./data-parsed/possibilities-keys-asymmetric.pickle", "rb") as fp:
            guess_words, answer_words = pickle.load(fp)

        # now verify that they are loaded correctly
        for i in range(len(answer_words)):
            assert answer_words[i] == guess_words[i]
        return guess_words, answer_words
    else:
        raise Exception(dictionary)


def print_tree_stats(path: str, dictionary: str):
    guess_words, answer_words = get_words_for_dictionary(dictionary)
    d = {}
    tree = {}
    root_word = path.split("/")[-1].split(".")[0]
    with open(path) as fp:
        tree = json.load(fp)
    for answer in answer_words:
        depth, _ = find_answer_in_tree(
            answer=answer,
            tree=tree,
            depth=1,
            guess_words=guess_words,
            answer_words=answer_words,
        )
        d[answer] = depth
    df = pd.DataFrame({"answer": d.keys(), "depth": d.values()})
    # print(df)
    mean_depth = np.mean(df["depth"])
    print(f"mean depth for tree rooted at {root_word} is {mean_depth:.2f}")
    max_depth = np.max(df["depth"])
    print(f"max depth for tree rooted at {root_word} is {max_depth}")
    total_size = np.sum(df["depth"])
    print(f"Total size of tree is {total_size}")
    return df


if __name__ == "__main__":
    from argparse import ArgumentParser
    parser = ArgumentParser()
    parser.add_argument("-f", "--file",
                        help="Tree file (in the same format spit out by decision_tree.py)")
    parser.add_argument("-c", "--convert",
                        action="store_true",
                        help="By default this program only checks the tree. With this option we also convert the tree into a human-readable form")
    args = parser.parse_args()

    # silence logging in find_answer_in_tree
    logging.basicConfig(level=logging.WARNING)

    dictionary = get_dictionary_from_filename(args.file)
    check_file(args.file, dictionary)
    print_tree_stats(args.file, dictionary)

    if args.convert:
        convert_file_to_human_readable(args.file, dictionary)