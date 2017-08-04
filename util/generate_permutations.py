#!/usr/bin/env python
# other comment about how this file works, plus probably an example of implementation

import json
import argparse
from argparse import ArgumentParser
from pprint import pprint
from itertools import product
import sys


# # def parse_args():
# parser = ArgumentParser(description='Create course permutations')
# parser.add_argument('--fields', action='', nargs=3)
#
def generate_permutations():
    parser = argparse.ArgumentParser()
    parser.add_argument('--fields', nargs=3, action="append", default=None,
                        help="Specify which fields to generate permutations on")
    parser.add_argument('filename')
    args = parser.parse_args()

    file = open(args.filename)
    permutation_data = json.load(file)

    # if no field arguments are given, just print out default data
    if not args.fields:
        default_permutation = permutation_data["default_data"]
        print default_permutation
    else:
        for permutation_choices in args.fields:
            first_field = permutation_data["permutation_data"][permutation_choices[0]]
            # print first_field
            second_field = permutation_data["permutation_data"][permutation_choices[1]]
            permutation_generation = [first_field, second_field]
            print list(product(*permutation_generation))





if __name__ == "__main__":
    generate_permutations()