#!/bin/python

__date__ = "2022-10-8"
__author__ = "Junbo Yang"
__email__ = "yang_junbo_hi@126.com"
__license__ = "MIT"

"""
The MIT License (MIT)

Copyright (c) 2022 Junbo Yang <yang_junbo_hi@126.com> <1806389316@pku.edu.cn>

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
"""

import math
import os
import argparse
import time
from functools import reduce
from math import log10
from itertools import product
from multiprocessing import Manager
from collections import defaultdict
import re
from concurrent.futures import ProcessPoolExecutor, as_completed
from operator import mul
from statistics import mean
from bisect import bisect_left
from optparse import OptionParser
import sys


def argsParse():
    parser = OptionParser('Usage: %prog -i [input] -r [sequence.fa] -o [output] \n \
                Options: {-f [0.6] -m [500] -n [200] -e [4] -p [9] -s [250,500] -g [0.4,0.6] -d [4] -a ","}' ,version="%prog 0.0.5")
    parser.add_option('-i', '--input',
                      dest='input',
                      help='Input file: degeprimer out.')

    parser.add_option('-r', '--ref',
                      dest='ref',
                      help='Reference sequence file: all the sequence in 1 fasta, for example: (Cluster_96_171.fa).')

    parser.add_option('-g', '--gc',
                      dest='gc',
                      default="0.4,0.6",
                      help="Filter primers by GC content. Default [0.4,0.6].")

    parser.add_option('-f', '--fraction',
                      dest='fraction',
                      default="0.6",
                      type="float",
                      help="Filter primers by match fraction. Default: 0.6. \n"
                           "Sometimes you need a small fraction to get output.")

    parser.add_option('-e', '--end',
                      dest='end',
                      default="4",
                      type="int",
                      help="Filter primers by degenerate base position. e.g. [-t 4] means I dont want degenerate base "
                           "appear at the end four bases when primer pre-filter. Default: 4.")

    parser.add_option('-p', '--proc',
                      dest='proc',
                      default="10",
                      type="int",
                      help="Number of process to launch.  default: 10.")

    parser.add_option('-s', '--size',
                      dest='size',
                      default="250,500",
                      help="Filter primers by PRODUCT size. Default [250,500].")

    parser.add_option('-d', '--dist',
                      dest='dist',
                      default=4,
                      type="int",
                      help='Filter param of hairpin, which means distance of the minimal paired bases. Default: 4. '
                           'Example:(number of X) AGCT[XXXX]AGCT.')

    parser.add_option('-a', '--adaptor',
                      dest='adaptor',
                      default="TCTTTCCCTACACGACGCTCTTCCGATCT,TCTTTCCCTACACGACGCTCTTCCGATCT",
                      type="str",
                      help='Adaptor sequence, which is used for NGS next. Hairpin or dimer detection for [adaptor--primer].'
                           '\n \ For example: TCTTTCCCTACACGACGCTCTTCCGATCT,TCTTTCCCTACACGACGCTCTTCCGATCT (Default). ''If '
                           'you dont want adaptor, use [","] ')

    parser.add_option('-m', '--maxseq',
                      dest='maxseq',
                      default=500,
                      type="int",
                      help='Limit of sequence number. Default: 500.\n'
                           'This param should consistent with [max_seq] in multi-alignment [muscle].')

    parser.add_option('-o', '--out',
                      dest='out',
                      help='Output file: candidate primers. e.g. [*].candidate.primers.txt.')
    (options, args) = parser.parse_args()
    if len(sys.argv) == 1:
        parser.print_help()
        sys.exit(1)
    elif options.input is None:
        parser.print_help()
        print("Input file must be specified !!!")
        sys.exit(1)
    elif options.ref is None:
        parser.print_help()
        print("Reference file must be specified !!!")
        sys.exit(1)
    elif options.out is None:
        parser.print_help()
        print("No output file provided !!!")
        sys.exit(1)
    return parser.parse_args()


TRANS_c = str.maketrans("ATCG", "TAGC")


def complement(seq):
    return seq.translate(TRANS_c)[::-1]


def symmetry(seq):
    if len(seq) % 2 == 1:
        return False
    else:
        F = seq[:int(len(seq) / 2)]
        R = complement(seq[int(len(seq) / 2):][::-1])
        if F == R:
            return True
        else:
            return False

TRANS = str.maketrans("ATGCRYMKSWHBVDN", "TACGYRKMSWDVBHN")

degenerate_base = {"R": ["A", "G"], "Y": ["C", "T"], "M": ["A", "C"], "K": ["G", "T"],
                   "S": ["G", "C"], "W": ["A", "T"], "H": ["A", "T", "C"], "B": ["G", "T", "C"],
                   "V": ["G", "A", "C"], "D": ["G", "A", "T"], "N": ["A", "T", "G", "C"]}

score_table = {"A": 1, "G": 1.1, "C": 1.2, "T": 1.4, "R": 2.1, "Y": 2.6, "M": 2.2,
               "K": 2.5, "S": 2.3, "W": 2.4, "H": 3.6, "B": 3.7, "V": 3.3, "D": 3.5, "N": 4.7}
# Martin Zacharias* "Base-Pairing and Base-Stacking Contributions to Double-Stranded DNA Formation"
# J. Phys. Chem. B 2020, 124, 46, 10345–10352

freedom_of_H_37_table = [[-0.7, -0.81, -0.65, -0.65],
                         [-0.67, -0.72, -0.8, -0.65],
                         [-0.69, -0.87, -0.72, -0.81],
                         [-0.61, -0.69, -0.67, -0.7]]

penalty_of_H_37_table = [[0.4, 0.575, 0.33, 0.73],
                         [0.23, 0.32, 0.17, 0.33],
                         [0.41, 0.45, 0.32, 0.575],
                         [0.33, 0.41, 0.23, 0.4]]

H_bonds_number = [[2, 2.5, 2.5, 2],
                  [2.5, 3, 3, 2.5],
                  [2.5, 3, 3, 2.5],
                  [2, 2.5, 2.5, 2]]

# adjust = {"A": 0.98, "T": 0.98, "C": 1.03, "G": 1.03}
#
# adjust_initiation = {"A": 2.8, "T": 2.8, "C": 1.82, "G": 1.82}
#
# adjust_terminal_TA = 0.4
#
# symmetry_correction = 0.4
adjust_initiation = {"A": 0.98, "T": 0.98, "C": 1.03, "G": 1.03}
adjust_terminal_TA = 0.4
# Symmetry correction applies only to self-complementary sequences.
# symmetry_correction = 0.4
symmetry_correction = 0.4

base2bit = {"A": 0, "C": 1, "G": 2, "T": 3}

di_nucleotides = set()

for i in base2bit.keys():
    single = i * 4
    di_nucleotides.add(single)
    for j in base2bit.keys():
        if i != j:
            di = (i + j) * 4
            di_nucleotides.add(di)
        for k in base2bit.keys():
            if i != j != k:
                tri = (i + j + k) * 3
                di_nucleotides.add(tri)
def dege_trans(sequence):
        seq = []
        cs = ""
        for s in sequence:
            if s not in degenerate_base:
                cs += s
            else:
                seq.append([cs + i for i in degenerate_base[s]])
                cs = ""
        if cs:
            seq.append([cs])
        return ("".join(i) for i in product(*seq))


def score_trans(sequence):
    return reduce(mul, [math.floor(score_table[x]) for x in list(sequence)])


def reversecomplement(seq):
    return seq.translate(TRANS)[::-1]


def Penalty_points(length, GC, d1, d2):
    return log10((2 ** length * 2 ** GC) / ((d1 + 0.1) * (d2 + 0.1)))


class Primers_filter(object):
    def __init__(self, ref_file, primer_file, adaptor, rep_seq_number=500, distance=4, outfile="",
                 size="300,700", position=9, GC="0.4,0.6", nproc=10, fraction=0.6):
        self.nproc = nproc
        self.primer_file = primer_file
        self.adaptor = adaptor
        self.size = size
        self.outfile = os.path.abspath(outfile)
        self.distance = distance
        self.Input_file = ref_file
        self.fraction = fraction
        self.GC = GC
        self.rep_seq_number = rep_seq_number
        self.number = self.get_number()
        self.position = position
        self.primers = self.parse_primers()
        self.resQ = Manager().Queue()
        self.pre_filter_primers = self.pre_filter()

    def parse_primers(self):
        primer_dict = {}
        with open(self.primer_file) as f:
            for i in f:
                if i.startswith("Pos"):
                    pass
                else:
                    i = i.strip().split("\t")
                    position = int(i[0])
                    primer_seq = i[5]
                    number = int(i[6])
                    fraction = round(int(i[6]) / self.number, 2)
                    primer_dict[position] = [primer_seq, fraction, number]
        return primer_dict

    ################# get_number #####################
    def get_number(self):
        from itertools import (takewhile, repeat)
        buffer = 1024 * 1024
        with open(self.Input_file, encoding="utf-8") as f:
            buf_gen = takewhile(lambda x: x, (f.read(buffer) for _ in repeat(None)))
            seq_number = int(sum(buf.count("\n") for buf in buf_gen) / 2)
            if seq_number > self.rep_seq_number != 0:
                print(seq_number, self.rep_seq_number)
                return self.rep_seq_number
            else:
                return seq_number

    ################# degenerate_seq #####################
    @staticmethod
    def degenerate_seq(sequence):
        seq = []
        cs = ""
        for s in sequence:
            if s not in degenerate_base:
                cs += s
            else:
                seq.append([cs + i for i in degenerate_base[s]])
                cs = ""
        if cs:
            seq.append([cs])
        return ("".join(i) for i in product(*seq))

    ################# Hairpin #####################
    def hairpin_check(self, primer):
        n = 0
        distance = self.distance
        check = "FALSE"
        while n <= len(primer) - 5 - 5 - distance:
            kmer = self.degenerate_seq(primer[n:n + 5])
            left = self.degenerate_seq(primer[n + 5 + distance:])
            for k in kmer:
                for l in left:
                    if re.search(reversecomplement(k), l):
                        check = "TRUE"
                        break
                if check == "TRUE":
                    break
            if check == "TRUE":
                break
            n += 1

        if check == "TRUE":
            return True
        else:
            return False

    ################# current_end #####################
    def current_end(self, primer, num=5, length=14):
        end_seq = set()
        for i in range(num, (num + length)):
            s = primer[-i:]
            if s:
                end_seq.union(set(self.degenerate_seq(s)))
        return end_seq

    ################# Free energy #####################
    def deltaG(self, sequence):
        Delta_G_list = []
        Na = 50
        for seq in self.degenerate_seq(sequence):
            Delta_G = 0
            for n in range(len(seq) - 1):
                i, j = base2bit[seq[n + 1]], base2bit[seq[n]]
                Delta_G += freedom_of_H_37_table[i][j] * H_bonds_number[i][j] + penalty_of_H_37_table[i][j]
            term5 = sequence[-2:]
            if term5 == "TA":
                Delta_G += adjust_initiation[seq[0]] + adjust_terminal_TA
            else:
                Delta_G += adjust_initiation[seq[0]]
            Delta_G -= (0.175 * math.log(Na / 1000, math.e) + 0.20) * len(seq)
            if symmetry(seq):
                Delta_G += symmetry_correction
            Delta_G_list.append(Delta_G)
        return round(max(Delta_G_list), 2)

    ################# Dimer #####################
    def dimer_check(self, primer_F, primer_R):
        current_end_set = self.current_end(primer_F).union(self.current_end(primer_R))
        primer_pairs = [primer_F, primer_R]
        dimer = False
        for pp in primer_pairs:
            for end in current_end_set:
                for p in self.degenerate_seq(pp):
                    idx = p.find(reversecomplement(end))
                    if idx >= 0:
                        end_length = len(end)
                        end_GC = end.count("G") + end.count("C")
                        end_d1 = 0
                        end_d2 = len(p) - len(end) - idx
                        Loss = Penalty_points(
                            end_length, end_GC, end_d1, end_d2)
                        delta_G = self.deltaG(end)
                        if Loss > 3 or delta_G < -5:
                            dimer = True
                            if dimer:
                                break
                if dimer:
                    break
        if dimer:
            return True
        else:
            return False

    ################# position of degenerate base #####################
    def dege_filter_in_term_N_bp(self, sequence):
        term = self.position
        if term == 0:
            term_base = ["A"]
        else:
            term_base = sequence[-term:]
        score = score_trans(term_base)
        if score > 1:
            return True
        else:
            return False

    ################# GC content #####################
    def GC_fraction(self, sequence):
        sequence_expand = self.degenerate_seq(sequence)
        GC_list = []
        for seq in sequence_expand:
            GC_list.append(round((list(seq).count("G") + list(seq).count("C")) / len(list(seq)), 3))
        GC_average = mean(GC_list)
        return GC_average

    ################# di_nucleotide #####################
    def di_nucleotide(self, primer):
        Check = "False"
        primers = self.degenerate_seq(primer)
        for m in primers:
            for n in di_nucleotides:
                if re.search(n, m):
                    Check = "True"
                    break
                else:
                    pass
            if Check == "True":
                break
        if Check == "True":
            return True
        else:
            return False

    ################# di_nucleotide #####################
    def GC_clamp(self, primer, num=4, length=13):
        check = False
        for i in range(num, (num + length)):
            s = primer[-i:]
            gc_fraction = self.GC_fraction(s)
            if gc_fraction > 0.6:
                check = True
                break
        if check:
            return True
        else:
            return False

    def pre_filter(self):
        limits = self.GC.split(",")
        min = float(limits[0])
        max = float(limits[1])
        min_cov = self.fraction
        candidate_primers_position = []
        primer_info = self.primers
        for primer_position in primer_info.keys():
            primer = primer_info[primer_position][0]
            coverage = primer_info[primer_position][1]
            if self.hairpin_check(primer):
                pass
            elif self.GC_fraction(primer) > max or self.GC_fraction(primer) < min:
                pass
            elif self.di_nucleotide(primer):
                pass
            elif coverage < min_cov:
                pass
            else:
                candidate_primers_position.append(primer_position)
        return sorted(candidate_primers_position)

    @staticmethod
    def closest(my_list, my_number1, my_number2):
        index_left = bisect_left(my_list, my_number1)
        # find the first element index in my_list which greater than my_number.
        if my_number2 > my_list[-1]:
            index_right = len(my_list) - 1  # This is index.
        else:
            index_right = bisect_left(my_list, my_number2) - 1
        return index_left, index_right

    def primer_pairs(self, primer_pairs):
        size_list = self.size.split(",")
        min_len = int(size_list[0])
        max_len = int(size_list[1])
        candidate_position = self.pre_filter_primers
        adaptor = self.adaptor.split(",")
        if int(candidate_position[-1]) - int(candidate_position[0]) < min_len:
            pass
        else:
            for start in range(len(candidate_position)):
                primerF_extend = adaptor[0] + self.primers[candidate_position[start]][0]
                if self.hairpin_check(primerF_extend):
                    pass
                elif self.dege_filter_in_term_N_bp(self.primers[candidate_position[start]][0]):
                    pass
                elif self.GC_clamp(self.primers[candidate_position[start]][0]):
                    pass
                else:
                    start_index, stop_index = self.closest(candidate_position, candidate_position[start]
                                                           + min_len, candidate_position[start] + max_len)
                    if start_index > stop_index:  # caution: all(var) > stop_index_start in bisect_left,
                        # you must stop immediately when stop_index_start > Product length
                        break
                    else:
                        for stop in range(start_index, stop_index + 1):
                            primerR_extend = adaptor[1] + reversecomplement(self.primers[candidate_position[stop]][0])
                            if self.hairpin_check(primerR_extend):
                                pass
                            elif self.dege_filter_in_term_N_bp(
                                    reversecomplement(self.primers[candidate_position[stop]][0])):
                                pass
                            elif self.GC_clamp(reversecomplement(self.primers[candidate_position[stop]][0])):
                                pass
                            else:
                                distance = int(candidate_position[stop]) - int(candidate_position[start]) + 1
                                if distance > int(max_len):
                                    break
                                elif int(min_len) <= distance <= int(max_len):
                                    if self.dimer_check(self.primers[candidate_position[start]][0],
                                                        reversecomplement(self.primers[candidate_position[stop]][0])):
                                        pass
                                    else:
                                        # primer_pairs.append((candidate_position[start], candidate_position[stop]))
                                        line = (self.primers[candidate_position[start]][0],
                                                reversecomplement(self.primers[candidate_position[stop]][0]), distance,
                                                min(self.primers[candidate_position[start]][2],
                                                    self.primers[candidate_position[stop]][2]),
                                                str(candidate_position[start])+":"+str(candidate_position[stop]))
                                        primer_pairs.append(line)
        #                                 self.resQ.put(line)
        # self.resQ.put(None)

        #  The queue in multiprocessing cannot be used for pool process pool, but there is a manager in multiprocessing.
        #  Inter process communication in the pool uses the queue in the manager. Manager().Queue().
        #  Queue. qsize(): returns the number of messages contained in the current queue;
        #  Queue. Empty(): returns True if the queue is empty, otherwise False;
        #  Queue. full(): returns True if the queue is full, otherwise False;
        #  Queue. get(): get a message in the queue, and then remove it from the queue,
        #                which can pass the parameter timeout.
        #  Queue.get_Nowait(): equivalent to Queue. get (False).
        #                If the value cannot be obtained, an exception will be triggered: Empty;
        #  Queue. put(): add a value to the data sequence to transfer the parameter timeout duration.
        #  Queue.put_Nowait(): equivalent to Queue. get (False). When the queue is full, an error is reported: Full.

    def run(self):
        p = ProcessPoolExecutor(self.nproc)  #
        primer_pairs = Manager().list()
        p.submit(
            self.primer_pairs(primer_pairs))  # This will submit all tasks to one place without blocking, and then each
        # thread in the thread pool will fetch tasks.
        ID = str(self.outfile)
        with open(self.outfile, "w") as fo:
            # headers = ["Primer_F_seq", "Primer_R_seq", "Product length", "Target number", "Primer_start_end"]
            # fo.write(ID + "\t" + "\t".join(headers) + "\t")
            fo.write(ID + "\t")
            for i in primer_pairs:
                fo.write("\t".join(map(str, i)) + "\t")
            # get results before shutdown. Synchronous call mode: call, wait for the return value, decouple, but slow.
            fo.write("\n")
            fo.close()
        p.shutdown()
        # After I run the main, I don't care whether the sub thread is alive or dead. With this parameter, after all
        # the sub threads are executed, the main function is executed get results after shutdown.
        # Asynchronous call mode: only call, unequal return values, coupling may exist, but the speed is fast.


def main():
    options, args = argsParse()
    primer_pairs = Primers_filter(ref_file=options.ref, primer_file=options.input, adaptor=options.adaptor,
                                  rep_seq_number=options.maxseq, distance=options.dist, outfile=options.out,
                                  size=options.size, position=options.end, fraction=options.fraction,
                                  nproc=options.proc)
    primer_pairs.run()


if __name__ == "__main__":
    e1 = time.time()
    main()
    e2 = time.time()
    print("INFO {} Total times: {}".format(time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time())),
                                           round(float(e2 - e1), 2)))
