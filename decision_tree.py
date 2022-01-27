import json
import logging
import os.path
import time
from typing import Dict, Iterable, List, Set, Tuple

import coloredlogs
import numpy as np
import pandas as pd
from tqdm import tqdm

from parse_data import read_all_answers, read_parsed_words
from play import LETTER_ABSENT
from possibilities_table import TABLE_PATH, integer_to_arr

ALL_LETTERS_CORRECT = (3 ** 5) - 1

# set this to true to enable debug printing
IS_DEBUG = True

# set this to logging.debug to hide it
PROGRESS_LOG_LEVEL = logging.INFO
# at what level to print. 3 is very verbose and 1 is too infrequent
MAX_PROGRESS_DEPTH = 3

# whether to use tqdm (progress bar) at a low depth
# this will mess up the output if debugging is enabled
# and obviously will slow down the solver a little
USE_TQDM_LOW_DEPTHS = True
# at which depth to enable the progress bar
# I do not recommend setting lower than 1 or 2
TQDM_DEPTH = 2

# whether to use optimization #3
# doesn't usually make sense to turn this on
# if we use a smaller dictionary, the solver is much faster without it
USE_OPT_3 = True
OPT_3_LOG_LEVEL = logging.DEBUG

# whether to use optimization #4
# doesn't always make sense to turn this on
# if we use a smaller dictionary, the solver is much faster without it
USE_OPT_4 = True
# at what level to output these messages
# note that logging.DEBUG will *hide* the messages. This is on purpose.
OPT_4_LOG_LEVEL = logging.DEBUG

# whether to time how quickly we're solving stuff
IS_TIMING_ENABLED = False
# at what depth to time
TIMING_DEPTH = 2

SORTED_GUESSES = []  # type: List[int]
WORDS = []  # type: List[str]


def get_black_letters(guesses: List[int], results: List[int], words: List[str]) -> Set[str]:
    """
    Once a letter is turned black from a guess, that letter cannot be used in any subsequent word.
    Return all the unusable letters
    NOTE: This is the only method that relies on words being strings rather than integers
    NOTE: This method is on the hot path so long as pick_next_guesses_it is on the hot path
    """
    black_letters = set([])
    for (guess, result) in zip(guesses, results):
        result_arr = integer_to_arr(result)
        word = words[guess]
        for (letter, val) in zip(word, result_arr):
            if val == LETTER_ABSENT:
                black_letters.add(letter)
    return black_letters


def get_chain(prev_guesses: List[int], prev_guess_results: List[int], depth: int) -> str:
    """
    Used for debugging.
    Print the entire chain of the decision tree so far.
    NOTE: accesses WORDS in global scope
    """
    chain = []
    for i in range(depth):
        guess = prev_guesses[i]
        guess_word = WORDS[guess]
        chain.append(guess_word)
        if i < len(prev_guess_results):
            chain.append(str(prev_guess_results[i]))
    s = f'depth = {depth}: path = ' + ' -> '.join(chain)
    return s


def print_chain(prev_guesses: List[int], prev_guess_results: List[int], depth: int) -> None:
    """
    Used for debugging.
    Print the entire chain of the decision tree so far.
    """
    s = get_chain(prev_guesses, prev_guess_results, depth)
    print(s)


def get_mean_partition(row: np.ndarray) -> float:
    _, counts = np.unique(row, return_counts=True)
    return np.mean(counts)


def get_worst_partition_bincount(row: np.ndarray) -> float:
    """An optimized version of get_worst_partition"""
    return np.bincount(row).max()


def get_mean_partition_arr(table: np.ndarray, possible_answers: Set[int]) -> np.ndarray:
    # select the columns of the table with the possible answers
    possible_answers_arr = np.array(list(possible_answers))
    m = table[:, possible_answers_arr]
    # compute the mean partition size for each row
    return np.apply_along_axis(get_mean_partition, axis=1, arr=m)


def get_worst_partition_arr(table: np.ndarray, possible_answers: Set[int]) -> np.ndarray:
    # select the columns of the table with the possible answers
    possible_answers_arr = np.array(list(possible_answers))
    m = table[:, possible_answers_arr]
    # compute the mean partition size for each row
    return np.apply_along_axis(get_worst_partition_bincount, axis=1, arr=m)


def NEW_pick_next_guesses_it(guesses: List[int], guess_results: List[int], table: np.ndarray, possible_answers: Set[int]) -> Iterable[int]:
    """Return an iterator over possible next guesses in order of our heuristic
    This method is about 40 times slower than `pick_next_guesses_it`
    Hopefully it returns values that are 40x better
    """
    possible_answers_arr = np.array(list(possible_answers))
    # select the columns of the table with the possible answers
    m = table[:, possible_answers_arr]

    # compute the largest partition size for each row
    # sort_arr = np.apply_along_axis(get_worst_partition_bincount, axis=1, arr=m)
    # compute the mean partition size for each row
    sort_arr = np.apply_along_axis(get_mean_partition, axis=1, arr=m)

    si = np.argsort(sort_arr)

    # assume that all guesses are valid for now
    valid_guesses = np.arange(table.shape[0])

    # use si to sort the valid guesses in order of the heuristic
    # smallest mean partition goes first
    sg = np.take_along_axis(valid_guesses, si, axis=0)

    for next_guess in sg:
        if next_guess in guesses:
            continue

        # logging.info(f"{next_guess} {words[next_guess]} {mean_partitions[next_guess]}")
        yield next_guess


def pick_next_guesses_it(guesses: List[int], guess_results: List[int], sorted_guesses: List[int], words: List[str]) -> Iterable[int]:
    """Return an iterator over possible next guesses.
    They are returned in the order that they are probably best.
    Invalid guesses are not returned.

    NOTE: This is on the hot path. This method will be called hundreds of thousands, if not millions, of times.
    """
    black_letters = get_black_letters(guesses, guess_results, words)

    for next_guess in sorted_guesses:
        if next_guess in guesses:
            continue

        w = words[next_guess]
        if any([letter in black_letters for letter in w]):
            continue

        yield next_guess


def NEW_find_possible_answers(guesses: List[int], guess_results: List[int], table: np.ndarray) -> Set[int]:
    """
    NOTE: this is much slower than `find_possible_answers`, maybe 20x
    """
    # each guess must have a corresponding result
    assert len(guesses) == len(guess_results)
    reachable = set([])

    if not guesses:
        l = np.arange(table.shape[0])
        return set(l)

    num_answers = table.shape[0]
    m = table[guesses]
    gr = np.array(guess_results)

    for i in np.arange(num_answers):
        if (m[:, i] == gr).all():
            reachable.add(i)
    return reachable


def find_possible_answers(guesses: List[int], guess_results: List[int], table: np.ndarray) -> Set[int]:
    """
    Return a list of answers that are still possible given this history
    I benchmarked this and it's pretty fast
    """
    assert len(guesses) == len(guess_results)

    # to start, we can reach all words
    reachable = set(np.arange(table.shape[0]))
    for i, guess in enumerate(guesses):
        result = guess_results[i]
        reachable_from_guess = set(np.where(table[guess] == result)[0])
        # print("Can reach %d answers from guess %d" % (len(reachable_from_guess), guess))
        reachable.intersection_update(reachable_from_guess)
    # print("Can reach %d answers total" % len(reachable))
    return reachable


def print_debug(s: str) -> None:
    if IS_DEBUG:
        print(s)

def print_chain_debug(*args) -> None:
    if IS_DEBUG:
        print_chain(*args)


def construct_tree(guesses: List[int], guess_results: List[int], table: np.ndarray, depth: int, possible_answers: Set[int]) -> Tuple[dict, Set[int], int]:
    """
    Try to construct the best tree starting from an initial guess.
    *best* is defined here as a tree that reaches the maximum number of possible answers

    :param guesses:             The guesses so far, in order
    :param guess_results:       The results of the those guesses, in order. Will be one fewer than guesses
    :param depth:               The depth of the tree. Should be the same as # of guesses
    :param possible_answers:    The set of possible answers remaining

    Return a tuple of 3 items:
        - tree ->               Map from a root word to possible results for that root word. Each action maps to another guess and so forth
                                The tree is guaranteed to have only one top-level element: the last guess
        - found_words ->        A set of all words that are reachable from this subtree.
                                Reachable means that, if this word is the answer, we can find a unique path to that word
        - num_states_opened ->  The number of states that we tried when creating this tree
                                This is a measure of how good our heuristic / search is
                                Does not necessarily translate to a shorter search in wall-clock time
    """
    assert depth > 0
    assert len(guesses) == depth
    assert len(guess_results) == depth - 1

    latest_guess = guesses[-1]

    # ---- this is all debug code
    # if len(possible_answers) > 50:
    #     path = get_chain(guesses, guess_results, depth)
    #     logging.info("%d possible answers at depth %d. path: %s", len(possible_answers), depth, path)
    # if depth <= 2:
    #     path = get_chain(guesses, guess_results, depth)
    #     logging.info("[d=%d] Path: %s", depth, path)
    #     w = words[latest_guess]
    #     print_debug(f"Creating a subtree with root {w} at depth {depth}...")
    #     print_chain_debug(guesses, guess_results, depth)
    #     print_debug("")
    # ---- this is all debug code

    action_map = {}  # type: Dict[int, dict]
    tree = {}  # type: Dict[int, dict]
    tree[int(latest_guess)] = action_map
    tree_found_words = set([])
    num_states_opened = 1  # we tried the root

    # logging.info(get_chain(guesses, guess_results, depth))

    if latest_guess in possible_answers:
        # include the root if it's a viable answer
        # note that we can guess words which are not viable to narrow down possibilities
        tree_found_words.add(latest_guess)

    if depth >= 6:
        return tree, tree_found_words, num_states_opened

    possible_results, counts = np.unique(table[latest_guess], return_counts=True)
    # a further optimization: we should try the partitions with the *most* possible answers *first*
    si = np.argsort(-1 * counts)
    possible_results = np.take_along_axis(possible_results, si, axis=0)

    if USE_TQDM_LOW_DEPTHS and depth == TQDM_DEPTH:
        it_r = tqdm(possible_results)
    else:
        it_r = possible_results

    if IS_TIMING_ENABLED and TIMING_DEPTH == depth:
        start = time.time()

    is_early_exit = False
    for guess_result in it_r:
        if guess_result == ALL_LETTERS_CORRECT and latest_guess in possible_answers:
            # we've guessed the word. we're good.
            # no need to add it to the decision tree
            tree_found_words.add(latest_guess)
            continue
        elif guess_result == ALL_LETTERS_CORRECT and latest_guess not in possible_answers:
            # logging.warning("We should not be looking here")
            continue

        possible_answers_for_result = np.where(table[latest_guess] == guess_result)[0]
        possible_answers_for_result_s = set(possible_answers_for_result)
        new_possible_answers = possible_answers.intersection(possible_answers_for_result_s)


        if not new_possible_answers:
            # this is a combo of guesses that simply doesn't yield any valid words remaining
            # so it's no need to add it to the decision tree

            # ws = [words[i] for i in guesses]
            # logging.info("No possible answers for this chain: %s", ", ".join(ws))
            # gr = guess_results + [guess_result]
            # logging.info("And these results %s", ", ".join([str(n) for n in gr]))
            continue

        # ---- this is all debug code
        # if depth < 2:
        #     print_debug("Searching for a word to reach all %d answers in subtree %d..." % (len(new_possible_answers), guess_result))
        #     print_chain_debug(guesses, guess_results, depth)
        #     print_debug("")
        # ---- this is all debug code

        # TODO: uses globals and violates scope
        next_guesses_it = pick_next_guesses_it(guesses, guess_results + [guess_result], SORTED_GUESSES, WORDS)
        best = 0
        best_subtree_found_words = set([])

        num_guesses_tried = 0

        # true iff we found a guess that solves this subtree (works with this guess result)
        is_subtree_solved = False

        is_opt_3_enabled = False
        is_opt_4_enabled = False

        if len(new_possible_answers) == 1:
            # optimization #1: if there is only 1 possible word
            # then we guess that word
            answer = list(new_possible_answers)[0]
            next_guesses_it = [answer]
            # logging.info("Applying optimization #1 at depth 5")
        elif depth == 5 and len(new_possible_answers) > 1:
            # optimization #2: if we have 1 guess remaining and there are many (>1) possible words
            # then we can just guess any of those words
            # it doesn't matter, we will only be able to reach one of them anyway
            # save time on not trying more possibilities
            logging.debug("Applying optimization #2 at depth 5 - early exit")
            is_early_exit = True
            break
        elif USE_OPT_3 and depth == 4:
            # optimization #3: we have only 2 guesses left
            # we need to pick the guess that divides the space such that, for all possible remaining answers, we can solve the puzzle using the last guess
            # i.e. we want all partitions to have size 1
            worst_partition_arr = get_worst_partition_arr(table, new_possible_answers)
            # is there any guess that has a worst partition of 1?
            good_guesses = np.where(worst_partition_arr == 1)[0]
            if good_guesses.size == 0:
                logging.log(OPT_3_LOG_LEVEL,
                            "Optimization #3 enabled: there is *no* good partition at depth 4. Exiting early.")
                is_early_exit = True
                break
                # # there are no good guesses
                # # just pick a random word
                # rw = list(new_possible_answers)[0]
                # next_guesses_it = [rw]
            else:
                # this is our optimal partition
                opt = good_guesses[0]
                next_guesses_it = [opt]
                logging.log(OPT_3_LOG_LEVEL,
                            "Optimization #3 enabled: Found the optimal partition at depth 4")
            is_opt_3_enabled = True
        elif USE_OPT_4 and depth <= 2:
            # instead of using our weak heuristic, use a slower but better heuristic to select guesses
            logging.log(OPT_4_LOG_LEVEL, "Optimization #4 enabled at depth %d", depth)
            next_guesses_it = NEW_pick_next_guesses_it(guesses, guess_results, table, new_possible_answers)
            is_opt_4_enabled = True

        for next_guess in next_guesses_it:
            # ---- this is all debug code
            if USE_OPT_4 and is_opt_4_enabled:
                logging.log(OPT_4_LOG_LEVEL,
                            "[d=%d] Optimization #4 enabled. Trying guess %d instead for guess_result %d",
                            depth, next_guess, guess_result)
            # ---- this is all debug code

            subtree, subtree_found_words, subtree_states_opened = construct_tree(
                guesses=guesses + [next_guess],
                guess_results=guess_results + [guess_result],
                table=table,
                possible_answers=new_possible_answers,
                depth=depth+1,
            )

            num_states_opened += subtree_states_opened

            if len(subtree_found_words) > best:
                # need to convert numpy type into python-native type for later serialization
                action_map[int(guess_result)] = subtree

                # update best
                best_subtree_found_words = subtree_found_words
                best = len(subtree_found_words)

            num_guesses_tried += 1

            if len(subtree_found_words) == len(new_possible_answers):
                is_subtree_solved = True
                break

        tree_found_words.update(best_subtree_found_words)
        # ---- this is all debug code
        # if USE_OPT_3 and is_opt_3_enabled:
        #     logging.log(OPT_3_LOG_LEVEL,
        #                  "Optimization #3 enabled. # guesses tried for subtree with guess_result %d: %d. (is subtree solved? %d)",
        #                  guess_result, num_guesses_tried, is_subtree_solved)
        if USE_OPT_4 and is_opt_4_enabled:
            logging.log(OPT_4_LOG_LEVEL,
                        "[d=%d] Optimization #4 enabled. # guesses tried for subtree with guess_result %d: %d (is subtree solved? %d)",
                         depth, guess_result, num_guesses_tried, is_subtree_solved)
        if depth < 4 and not is_subtree_solved:
            # we don't want to print all the lower failures since that would be annoying
            path = get_chain(guesses, guess_results + [guess_result], depth)
            logging.error("[d=%d] subtree not solved: %s", depth, path)
        # if not is_opt_3_enabled and depth == 4:
        #     logging.info("Optimization #3 disabled at level 4. # guesses tried for subtree with guess_result %d: %d", guess_result, num_guesses_tried)
        # ---- this is all debug code

        if not is_subtree_solved:
            # early exit. don't even bother trying the other answers
            # ---- this is all debug code
            # if depth <= 3:
            #     path = get_chain(guesses, guess_results + [guess_result], depth)
            #     logging.error("path %s is a dead end. early stopping for word %s.", path, WORDS[latest_guess])
            # ---- this is all debug code
            is_early_exit = True
            break

    # ---- this is all debug code
    if not is_early_exit and depth <= MAX_PROGRESS_DEPTH:
        path = get_chain(guesses[:-1], guess_results, depth - 1)
        logging.log(PROGRESS_LOG_LEVEL,
                    "Guess %s solves subtree: %s", WORDS[latest_guess], path)
    # ---- this is all debug code

    # ----- this is all debug code ----
    # if depth < 2:
    #     w = words[latest_guess]
    #     num_reachable_now = len(tree_found_words)
    #     num_reachable_ideal = len(possible_answers)
    #     print_debug(f"SUMMARY: finished subtree at depth {depth} and root {w}. Can reach {num_reachable_now}/{num_reachable_ideal} words in subtree")
    # ----- this is all debug code ----


    if IS_TIMING_ENABLED and TIMING_DEPTH == depth:
        stop = time.time()
        path = get_chain(guesses, guess_results, depth)
        logging.info("Expanded %d states at depth %d. Took %.2f seconds. Path: %s", num_states_opened, depth, stop - start, path)


    return tree, tree_found_words, num_states_opened


def check_is_reachable(guesses: List[int], guess_results: List[int], table: np.ndarray, target_word: int) -> bool:
    logging.warning("This is a debug function and should not be run when going for speed")
    is_reachable = True
    print(f"Checking reachability of answer {target_word}...")
    for i, guess in enumerate(guesses):
        expected = guess_results[i]
        actual = table[guess, target_word]
        if expected == actual:
            print(f"{i + 1}. Reachable from guess {guess}")
        else:
            print(f"{i + 1}. Not reachable from guess {guess}. Expected {expected} ({integer_to_arr(expected)}), actual {actual} ({integer_to_arr(actual)})")
            is_reachable = False
    return is_reachable


def solve(dictionary: str, first_word: str):
    logging.info("Using dictionary '%s'", dictionary)
    logging.info("Building decision tree using root word %s", first_word)

    words = []  # type: List[str]
    if dictionary == "full":
        words = read_parsed_words()
    else:
        words = [word.lower() for word in read_all_answers()]
    print(f"Loaded {len(words)} words")
    # NOTE: this is bad practice but it is accessed in the global scope
    global WORDS
    WORDS = words

    table = np.zeros(shape=(1, 1))
    if dictionary == "full":
        table = np.load(TABLE_PATH)  # type: np.ndarray
    else:
        TABLE_PATH_CHEATING = "./data-parsed/possibilities-table-cheating-base-3.npy"
        table = np.load(TABLE_PATH_CHEATING)  # type: np.ndarray
    print(f"Loaded {table.shape} table")

    mean_part_df = None
    cache_path = f"cache/mean_partition-{args.dictionary}.parquet"
    if not os.path.exists(cache_path):
        df = pd.DataFrame(table, index=words, columns=words)
        df['word_index'] = np.arange(len(words))
        print("Computing mean partition...")
        mean_part_df = df.apply(get_mean_partition, axis=1)
        mean_part_df = pd.DataFrame(mean_part_df, columns=['mean_partition'])
        mean_part_df['word_index'] = np.arange(len(words))
        print(mean_part_df.sort_values(by='mean_partition'))
        mean_part_df.to_parquet(cache_path)
        print(f"Saved mean partition df to file {cache_path}")
    else:
        mean_part_df = pd.read_parquet(cache_path)
        print("Loaded mean partition DF from cache")

    # lower score is better
    # sort word indexes based on their score in above score dict
    # sorted in ascending order (lowest score first)
    # NOTE: this is bad practice but it is accessed in the global scope
    global SORTED_GUESSES
    SORTED_GUESSES = mean_part_df.sort_values('mean_partition')['word_index'].values

    possible_words = set([i for i in range(len(words))])

    root_word_index = words.index(first_word)

    print("Building tree...")
    tree, found_words, num_states_opened = construct_tree(
        guesses=[root_word_index],
        guess_results=[],
        table=table,
        depth=1,
        possible_answers=possible_words,
    )

    print("Decision tree has been built")
    print(f"# states opened: {num_states_opened:,}")
    print("Found %d / %d words" % (len(found_words), len(words)))
    if len(found_words) == len(words):
        print("Success! Decision tree is full!")

    out_path = f"out/decision-trees/{dictionary}/{first_word}.json"
    with open(out_path, "w") as fp:
        json.dump(tree, fp, indent=4, sort_keys=True)
    print(f"Wrote tree to {out_path}")


def solve_all_cheating():
    words = read_all_answers()
    for word in tqdm(words):
        solve(dictionary="answers", first_word=word)


if __name__ == "__main__":
    coloredlogs.install()
    logging.basicConfig(level=logging.INFO)

    DEFAULT_ROOT_WORD = "aesir"
    DEFAULT_CHEATING_ROOT_WORD = "crane"

    from argparse import ArgumentParser
    parser = ArgumentParser()
    parser.add_argument(
        "-f", "--first-word", type=str,
        default=None,
        help="The root word to try to build our tree",
    )
    parser.add_argument(
        "-d",
        "--dictionary",
        choices=["full", "answers"],
        default="answers",
        help="The dictionary to use. Can either use the full dictionary (~13k words) or the cheating answers dictionary (~2300 words, default)"
    )
    args = parser.parse_args()

    first_word = DEFAULT_ROOT_WORD
    if args.first_word is None:
        if args.dictionary == "full":
            first_word = DEFAULT_ROOT_WORD
        else:
            first_word = DEFAULT_CHEATING_ROOT_WORD
    else:
        first_word = args.first_word

    solve(dictionary=args.dictionary, first_word=args.first_word)
    # solve_all_cheating()
