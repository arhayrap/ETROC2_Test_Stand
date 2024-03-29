#!/usr/bin/env python3
import struct
import numpy as np
import aiofiles
import asyncio
import os
import random  # For randint
import uhal
import argparse
import sys
import time
import pdb
from time import sleep
from tamalero.utils import get_kcu

#IPB_PATH = "ipbusudp-2.0://192.168.0.10:50001?max_payload_size=1500"
IP_ADDRESS = "192.168.0.10" # THE IP ADDRESS OF THE DAQ ASSISTANT
IPB_PATH = f"ipbusudp-2.0://{IP_ADDRESS}:50001"
ADR_TABLE = "./address_table/generic/etl_test_fw.xml"

def get_kcu_flag():
    return open(f"../../ScopeHandler/Lecroy/Acquisition/running_acquitision.txt").read()

def get_occupancy(hw, rb):
    try:
        occupancy = hw.getNode(f"READOUT_BOARD_{rb}.RX_FIFO_OCCUPANCY").read()
        hw.dispatch()
        occ = occupancy.value()
    except uhal._core.exception:
        print("uhal UPDP error when trying to get occupancy. Returning 0.")
        occ = 0
    return occ * 4  # not sure where the factor of 4 comes from, but it's needed

def stream_daq(kcu, rb=0, l1a_rate=1000, run_time=10, n_events=1000, superblock=100, block=250, run=1, ext_l1a=False):

    uhal.disableLogging()
    hw = kcu.hw  #uhal.getDevice("kcu105_daq", IPB_PATH, "file://" + ADR_TABLE)

    rate_setting = l1a_rate / 25E-9 / (0xffffffff) * 10000
    print(rate_setting)

    # reset fifo
    hw.getClient().write(hw.getNode(f"READOUT_BOARD_{rb}.FIFO_RESET").getAddress(), 0x1)
    hw.dispatch()

    # set l1a rate
    hw.getNode("SYSTEM.L1A_RATE").write(int(rate_setting))
    hw.dispatch()

    if ext_l1a:
        # enable external trigger
        hw.getNode("SYSTEM.EN_EXT_TRIGGER").write(0x1)
        hw.dispatch()

    start = time.time()

    data = []

    occupancy = 0
    f_out = f"ETROC_output/output_run_{run}.dat"
    # ev_index = 0
    occupancy_block = []
    # blocks = 0
    with open(f_out, mode="wb") as f:
        # while (start + run_time > time.time()):
        iteration = 0
        Running = get_kcu_flag()
        while (Running == "False"):
            if iteration == 0:
                print("Waiting for the scope.")
            Running = get_kcu_flag()
            iteration += 1

        Running = get_kcu_flag()
        print(Running)
        while (Running != "False"):
            Running = get_kcu_flag()
            # print(Running)
            # print(f"Event: {ev_index} / {n_events}")
            num_blocks_to_read = 0
            occupancy = get_occupancy(hw, rb)
            num_blocks_to_read = occupancy // block
            occupancy_block.append(num_blocks_to_read)
            # blocks += num_blocks_to_read
            # print(f"Number of blocks: {blocks}.")

            # print(occupancy)
            # if (occupancy >= block):
            #     ev_index += 1

            # read the blocks
            if (num_blocks_to_read):
                try:
                    reads = num_blocks_to_read * [hw.getNode("DAQ_RB0").readBlock(block)]
                    hw.dispatch()
                    for read in reads:
                        data += read.value()
                except uhal._core.exception:
                    print("uhal UDP error in reading FIFO")

                # Write data to disk
                try:
                    f.write(struct.pack('<{}I'.format(len(data)), *data))
                    data = []
                except:
                    print("Error writing to file")
        
        print("Resetting L1A rate back to 0")
        hw.getNode("SYSTEM.L1A_RATE").write(0)
        hw.dispatch()
        
        # Read data that might still be in the FIFO
        occupancy = get_occupancy(hw, rb)
        print(f"Occupancy before last read: {occupancy}")
        reads = [hw.getNode("DAQ_RB0").readBlock(occupancy)]
        hw.dispatch()
        for read in reads:
            data += read.value()

        #print(data)
        occupancy = get_occupancy(hw, rb)
        while occupancy>0:
            print("Found stuff in FIFO. This should not have happened!")
            num_blocks_to_read = occupancy // block
            last_block = occupancy % block
            if num_blocks_to_read > 0:
                print(occupancy, num_blocks_to_read, last_block)
            if (num_blocks_to_read or last_block):
                reads = num_blocks_to_read * [hw.getNode("DAQ_RB0").readBlock(block)]
                reads += [hw.getNode("DAQ_RB0").readBlock(last_block)]
                hw.dispatch()
                for read in reads:
                    data += read.value()
            occupancy = hw.getNode(f"READOUT_BOARD_{rb}.RX_FIFO_OCCUPANCY").read()
            hw.dispatch()

        # Get some stats
        timediff = time.time() - start
        speed = 32*len(data)  / timediff / 1E6
        occupancy = hw.getNode(f"READOUT_BOARD_{rb}.RX_FIFO_OCCUPANCY").read()
        lost = hw.getNode(f"READOUT_BOARD_{rb}.RX_FIFO_LOST_WORD_CNT").read()
        rate = hw.getNode(f"READOUT_BOARD_{rb}.PACKET_RX_RATE").read()
        l1a_rate_cnt = hw.getNode("SYSTEM.L1A_RATE_CNT").read()
        hw.dispatch()

        print("L1A rate = %f kHz" % (l1a_rate_cnt.value()/1000.0))
        print("Occupancy=%d words" % occupancy.value())
        print("Lost events=%d events" % lost.value())
        print("Packet rate=%d Hz" % rate.value())
        print("Speed = %f Mbps" % speed)

        # write to disk
        f.write(struct.pack('<{}I'.format(len(data)), *data))

    hw.getClient().write(hw.getNode(f"READOUT_BOARD_{rb}.FIFO_RESET").getAddress(), 0x1)
    hw.dispatch()

    if ext_l1a:
        # disable external trigger again
        hw.getNode("SYSTEM.EN_EXT_TRIGGER").write(0x0)
        hw.dispatch()

    return f_out


if __name__ == '__main__':

    argParser = argparse.ArgumentParser(description = "Argument parser")
    argParser.add_argument('--kcu', action='store', default='192.168.0.10', help="KCU address")
    argParser.add_argument('--rb', action='store', default=0, type=int, help="RB number (default 0)")
    argParser.add_argument('--l1a_rate', action='store', default=1000, type=int, help="L1A rate in Hz")
    argParser.add_argument('--ext_l1a', action='store_true', help="Enable external trigger input")
    argParser.add_argument('--run_time', action='store', default=10, type=int, help="Time in [s] to take data")
    argParser.add_argument('--n_events', action='store', default=1000, type=int, help="N events")
    argParser.add_argument('--run', action='store', default=1, type=int, help="Run number")
    args = argParser.parse_args()

    rb = int(args.rb)
    kcu = get_kcu(args.kcu)
    unit_time = 0.1

    print(f"Taking data now.\n ...")

    print(f"Resetting global event counter if RB #{rb}")
    kcu.write_node(f"READOUT_BOARD_{rb}.EVENT_CNT_RESET", 0x1)

    f_out = stream_daq(kcu, l1a_rate=args.l1a_rate, run_time=unit_time*args.n_events, n_events = args.n_events, run=args.run, ext_l1a=args.ext_l1a)

    print(f"Run {args.run} has ended.")
    print(f"Stored data in file: {f_out}")
    nevents = kcu.read_node(f"READOUT_BOARD_{rb}.EVENT_CNT").value()
    print(f"Recorded nevents={nevents}")
    # NOTE this would be the place to also dump the ETROC configs
