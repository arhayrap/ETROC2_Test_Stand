from tamalero.ETROC import ETROC
from tamalero.ETROC_Emulator import ETROC2_Emulator as software_ETROC2
from tamalero.DataFrame import DataFrame
from tamalero.utils import get_kcu
from tamalero.ReadoutBoard import ReadoutBoard
from tamalero.colors import red, green, yellow

import numpy as np
from scipy.optimize import curve_fit
from matplotlib import pyplot as plt
from tqdm import tqdm

import os
import sys
import json
import time
from yaml import load, dump
try:
    from yaml import CLoader as Loader, CDumper as Dumper
except ImportError:
    from yaml import Loader, Dumper

# ====== HELPER FUNCTIONS ======

# run N L1A's and return packaged ETROC2 dataformat
def run(ETROC, N, fifo=None):
    # currently uses the software ETROC to produce fake data
    if ETROC.isfake:
        return ETROC2.run(N)
    else:
        fifo.send_l1a(N)
        return fifo.pretty_read(None, raw=True)


def toPixNum(row, col, w):
    return col*w+row


def fromPixNum(pix, w):
    row = pix%w
    col = int(np.floor(pix/w))
    return row, col


def sigmoid(k,x,x0):
    return 1/(1+np.exp(k*(x-x0)))


# take x,y values and perform fit to sigmoid function
# return steepness(k) and mean(x0)
def sigmoid_fit(x_axis, y_axis):
    res = curve_fit(
        #sigmoid,
        lambda x,a,b: 1/(1+np.exp(a*(x-b))),  # for whatever reason this fit only works with a lambda function?
        x_axis-x_axis[0],
        y_axis,
        maxfev=10000,
    )
    return res[0][0], res[0][1]+x_axis[0]


# parse ETROC dataformat into 1D list of # of hits per pixel
def parse_data(data, N_pix):
    results = np.zeros(N_pix)
    pix_w = int(round(np.sqrt(N_pix)))

    for word in data:
        datatype, res = DF.read(word)
        if datatype == 'data':
            pix = toPixNum(res['row_id'], res['col_id'], pix_w)
            results[pix] += 1

    return results


def vth_scan(ETROC2, vth_min=693, vth_max=709, vth_step=1, decimal=False, fifo=None, absolute=False):
    N_l1a    =  3200 # how many L1As to send
    vth_min  =   vth_min # scan range
    vth_max  =   vth_max
    if not decimal:
        vth_step =   ETROC2.DAC_step # step size
    else:
        vth_step = vth_step
    N_steps  = int((vth_max-vth_min)/vth_step)+1 # number of steps
    N_pix    = 16*16 # total number of pixels

    vth_axis    = np.linspace(vth_min, vth_max, N_steps)
    run_results = np.empty([N_steps, N_pix])

    for vth in vth_axis:
        print(f"Working on threshold {vth=}")
        if decimal:
            ETROC2.wr_reg('DAC', int(vth), broadcast=True)
            #print("Acc value", ETROC2.get_ACC(row=0, col=0)) #doesn't work here
        else:
            ETROC2.set_Vth_mV(vth)
        i = int((vth-vth_min)/vth_step)
        run_results[i] = parse_data(run(ETROC2, N_l1a, fifo=fifo), N_pix)

    # transpose so each 1d list is for a pixel & normalize
    if absolute:
        run_results = run_results.transpose()
    else:
        run_results = run_results.transpose()/N_l1a
    return [vth_axis.tolist(), run_results.tolist()]


if __name__ == '__main__':

    # initiate
    ETROC2 = software_ETROC2()  # currently using Software ETROC2 (fake)
    print("ETROC2 emulator instantiated, base configuration successful")
    DF = DataFrame('ETROC2')

    # argsparser
    import argparse
    argParser = argparse.ArgumentParser(description = "Argument parser")
    argParser.add_argument('--test_readwrite', action='store_true', default=False, help="Test simple read/write functionality?")
    argParser.add_argument('--test_chip', action='store_true', default=False, help="Test simple read/write functionality for real chip?")
    argParser.add_argument('--vth', action='store_true', default=False, help="Parse Vth scan plots?")
    argParser.add_argument('--rerun', action='store_true', default=False, help="Rerun Vth scan and overwrite data?")
    argParser.add_argument('--fitplots', action='store_true', default=False, help="Create individual vth fit plots for all pixels?")
    argParser.add_argument('--kcu', action='store', default='192.168.0.10', help="IP Address of KCU105 board")
    argParser.add_argument('--module', action='store', default=0, choices=['1','2','3'], help="Module to test")
    argParser.add_argument('--host', action='store', default='localhost', help="Hostname for control hub")
    argParser.add_argument('--partial', action='store_true', default=False, help="Only read data from corners and edges")
    argParser.add_argument('--qinj', action='store_true', default=False, help="Run some charge injection tests")
    argParser.add_argument('--qinj_vth_scan', action='store_true', default=False, help="Run some charge injection tests")
    argParser.add_argument('--hard_reset', action='store_true', default=False, help="Hard reset of selected ETROC2 chip")
    argParser.add_argument('--scan', action='store', default=['full'], choices=['none', 'full', 'simple'], help="Which threshold scan to run with ETROC2")
    argParser.add_argument('--mode', action='store', default=['dual'], choices=['dual', 'single'], help="Port mode for ETROC2")
    args = argParser.parse_args()


    if args.test_readwrite:
        # ==============================
        # === Test simple read/write ===
        # ==============================
        print("<--- Test simple read/write --->")
        print("Testing read/write to addresses...")

        test_val = 0x2
        print(f"Broadcasting {test_val=} to CLSel in-pixel registers")
        ETROC2.wr_reg('CLSel', test_val, broadcast=True)
        assert ETROC2.rd_reg('CLSel', row=2, col=3) == test_val, "Did not read back the expected value"
        print("Test passed.\n")

        test_val = 2**8 + 2**5
        print(f"Broadcasting {test_val=} to DAC in-pixel registers")
        ETROC2.wr_reg('DAC', test_val, broadcast=True)
        assert ETROC2.rd_reg('DAC', row=5, col=4) == test_val, "Did not read back the expected value"
        print("Test passed.\n")

        test_val = 2**11 + 2**5
        print(f"Trying to broadcast too large value {test_val=} to DAC in-pixel registers")
        try:
            ETROC2.wr_reg('DAC', test_val, broadcast=True)
            raise NotImplementedError("Test failed.")
        except RuntimeError:
            print("Succesfully failed, as expected.")
            pass
        print("Test passed.\n")

        test_val = 700.25
        print(f"Trying to set the threshold to {test_val=}mV")
        ETROC2.set_Vth_mV(test_val)
        read_val = ETROC2.get_Vth_mV(row=4, col=5)
        if abs(read_val-test_val)>ETROC2.DAC_step:
            raise RuntimeError("Returned discriminator threshold is off.")
        else:
            print(f"Threshold is currently set to {read_val=} mV")
            print("Test passed.\n")

    elif args.test_chip:
        kcu = get_kcu(args.kcu, control_hub=True, host=args.host, verbose=False)
        if (kcu == 0):
            # if not basic connection was established the get_kcu function returns 0
            # this would cause the RB init to fail.
            sys.exit(1)

        rb_0 = ReadoutBoard(0, kcu=kcu, config='modulev0')
        data = 0xabcd1234
        kcu.write_node("LOOPBACK.LOOPBACK", data)
        if (data != kcu.read_node("LOOPBACK.LOOPBACK")):
            print("No communications with KCU105... quitting")
            sys.exit(1)

        is_configured = rb_0.DAQ_LPGBT.is_configured()
        if not is_configured:
            print("RB is not configured, exiting.")
            exit(0)

        from tamalero.Module import Module

        # FIXME the below code is still pretty stupid
        modules = []
        for i in [1,2,3]:
            m_tmp = Module(rb=rb_0, i=i)
            if m_tmp.ETROCs[0].connected:  # NOTE assume that module is connected if first ETROC is connected
                modules.append(m_tmp)

        print(f"Found {len(modules)} connected modules")
        if int(args.module) > 0:
            module = int(args.module)
        else:
            module = 1

        print(f"Will proceed with testing Module {module}")
        print("Module status:")
        modules[module-1].show_status()

        etroc = modules[module-1].ETROCs[0]
        if args.hard_reset:
            etroc.reset(hard=True)
            etroc.default_config()

        if args.mode == 'single':
            print(f"Setting the ETROC in single port mode ('right')")
            etroc.set_singlePort("right")
            etroc.set_mergeTriggerData("separate")
        elif args.mode == 'dual':
            print(f"Setting the ETROC in dual port mode ('both')")
            etroc.set_singlePort("both")
            etroc.set_mergeTriggerData("merge")

        #etroc = ETROC(rb=rb_0, i2c_adr=96, i2c_channel=1, elinks={0:[0,2]})
        
        print("\n - Checking peripheral configuration:")
        etroc.print_perif_conf()

        print("\n - Checking peripheral status:")
        etroc.print_perif_stat()

        print("\n - Running pixel sanity check:")
        res = etroc.pixel_sanity_check(verbose=False)
        if res:
            print("Passed!")
        else:
            print("Failed")

        print("\n - Running pixel random check:")
        res = etroc.pixel_random_check(verbose=False)
        if res:
            print("Passed!")
        else:
            print("Failed")

        print("\n - Checking configuration for pixel (4,5):")
        etroc.print_pixel_conf(row=4, col=5)

        print("\n - Checking status for pixel (4,5):")
        etroc.print_pixel_stat(row=4, col=5)

        ## pixel broadcast
        print("\n - Checking pixel broadcast.")
        etroc.wr_reg('workMode', 0, broadcast=True)
        tmp = etroc.rd_reg('workMode', row=10, col=10)
        etroc.wr_reg('workMode', 1, broadcast=True)
        test0 = True
        for row in range(16):
            for col in range(16):
                test0 &= (etroc.rd_reg('workMode', row=row, col=col) == 1)
        tmp2 = etroc.rd_reg('workMode', row=10, col=10)
        tmp3 = etroc.rd_reg('workMode', row=3, col=12)
        etroc.wr_reg('workMode', 0, broadcast=True)
        tmp4 = etroc.rd_reg('workMode', row=10, col=10)
        test1 = (tmp != tmp2)
        test2 = (tmp2 == tmp3)
        test3 = (tmp == tmp4)
        if test0 and test1 and test2 and test3:
            print("Passed!")
        else:
            print(f"Failed: {test0=}, {test1=}, {test2=}, {test3=}")

        # NOTE below is WIP code for tests of the actual data readout
        from tamalero.FIFO import FIFO
        from tamalero.DataFrame import DataFrame
        df = DataFrame()
        # NOTE this is for single port tests right now, where we only get elink 2
        fifo = FIFO(rb=rb_0)
        fifo.select_elink(2)
        fifo.ready()

        print("\n - Checking elinks")
        locked = kcu.read_node(f"READOUT_BOARD_0.ETROC_LOCKED").value()
        if (locked & 0b101) == 5:
            print(green('Both elinks (0 and 2) are locked.'))
        elif (locked & 1) == 1:
            print(yellow('Only elink 0 is locked.'))
        elif (locked & 4) == 4:
            print(yellow('Only elink 2 is locked.'))
        else:
            print(red('No elink is locked.'))

        fifo.send_l1a(10)
        _ = fifo.pretty_read(df)
        etroc.reset()

        print("\n - Getting internal test data")

        etroc.wr_reg("selfTestOccupancy", 2, broadcast=True)
        if not args.partial:
            etroc.wr_reg("workMode", 0x1, broadcast=True)
        else:
            etroc.wr_reg("selfTestOccupancy", 70, broadcast=True)
            etroc.wr_reg("workMode", 0x0, broadcast=True)
            ## center pixels
            #etroc.wr_reg("workMode", 0x1, row=15, col=7)
            etroc.wr_reg("workMode", 0x1, row=7, col=7)
            etroc.wr_reg("workMode", 0x1, row=7, col=8)
            etroc.wr_reg("workMode", 0x1, row=8, col=7)
            etroc.wr_reg("workMode", 0x1, row=8, col=8)
            # corner pixels
            etroc.wr_reg("workMode", 0x1, row=0, col=0)
            etroc.wr_reg("workMode", 0x1, row=15, col=15)
            etroc.wr_reg("workMode", 0x1, row=0, col=15)
            etroc.wr_reg("workMode", 0x1, row=15, col=0)
            # edge pixels
            etroc.wr_reg("workMode", 0x1, row=7, col=0)
            etroc.wr_reg("workMode", 0x1, row=8, col=0)
            etroc.wr_reg("workMode", 0x1, row=0, col=7)
            etroc.wr_reg("workMode", 0x1, row=0, col=8)
            etroc.wr_reg("workMode", 0x1, row=7, col=15)
            etroc.wr_reg("workMode", 0x1, row=8, col=15)
            etroc.wr_reg("workMode", 0x1, row=15, col=7)
            etroc.wr_reg("workMode", 0x1, row=15, col=8)

        etroc.wr_reg("onChipL1AConf", 0x2)  # NOTE: internal L1A is around 1MHz, so we're only turning this on for the shortest amount of time.
        etroc.wr_reg("onChipL1AConf", 0x0)
        test_data = []
        while fifo.get_occupancy() > 0:
            test_data += fifo.pretty_read(df)

        import hist
        import matplotlib.pyplot as plt
        import mplhep as hep
        plt.style.use(hep.style.CMS)

        hits_total = np.zeros((16,16))
        row_axis = hist.axis.Regular(16, -0.5, 15.5, name="row", label="row")
        col_axis = hist.axis.Regular(16, -0.5, 15.5, name="col", label="col")
        hit_matrix = hist.Hist(col_axis,row_axis)
        n_events_total = 0
        n_events_hit   = 0
        n_events_err   = 0
        for d in test_data:
            if d[0] == 'trailer':
                n_events_total += 1
                if d[1]['hits'] > 0:
                    n_events_hit += 1
            if d[0] == 'data':
                hit_matrix.fill(row=d[1]['row_id'], col=d[1]['col_id'])
                hits_total[d[1]['row_id']][d[1]['col_id']] += 1
                if d[1]['row_id'] != d[1]['row_id2']:
                    print("Unpacking error in row ID")
                    n_events_err += 1
                if d[1]['col_id'] != d[1]['col_id2']:
                    print("Unpacking error in col ID")
                    n_events_err += 1
                if d[1]['test_pattern'] != 0xaa:
                    print(f"Unpacking error in test pattern, expected 0xAA but got {d[1]['test_pattern']=}")
                    n_events_err += 1

        print(f"Got number of total events {n_events_total=}")
        print(f"Events with at least one hit {n_events_hit=}")
        print(f"Events with some error in data unpacking {n_events_err=}")

        plot_dir = './output/'
        fig, ax = plt.subplots(1,1,figsize=(7,7))
        hit_matrix.plot2d(
            ax=ax,
        )
        ax.set_ylabel(r'$Row$')
        ax.set_xlabel(r'$Column$')
        hep.cms.label(
                "ETL Preliminary",
                data=True,
                lumi='0',
                com=0,
                loc=0,
                ax=ax,
                fontsize=15,
            )
        name = 'hit_matrix_internal_test_pattern'
        fig.savefig(os.path.join(plot_dir, "{}.pdf".format(name)))
        fig.savefig(os.path.join(plot_dir, "{}.png".format(name)))

        fifo.reset()
        print("\n - Testing fast command communication - Sending two L1As")
        fifo.send_l1a(2)
        for x in fifo.pretty_read(df):
            print(x)

        #etroc.QInj_unset(broadcast=True)
        fifo.reset()
        if not args.partial:
            print("Will use workMode 1 to get some occupancy (no noise or charge injection)")
            etroc.wr_reg("workMode", 0x1, broadcast=True)  # this was missing
            for j in range(5):
                print(j)
                ### Another occupancy map
                i = 0
                occupancy = 0
                print("\n - Will send L1As until FIFO is full.")

                #etroc.QInj_set(30, 0, row=3, col=3, broadcast=False)
                start_time = time.time()
                with tqdm(total=65536) as pbar:
                    while not fifo.is_full():
                        fifo.send_l1a()
                        #fifo.send_QInj(delay=j)
                        #fifo.send_QInj()
                        i +=1
                        if i%100 == 0:
                            tmp = fifo.get_occupancy()
                            pbar.update(tmp-occupancy)
                            occupancy = tmp
                        #if time.time()-start_time>5:
                        #    print("Time out")
                        #    break

                test_data = []
                while fifo.get_occupancy() > 0:
                    test_data += fifo.pretty_read(df)

                hits_total = np.zeros((16,16))
                hit_matrix = hist.Hist(col_axis,row_axis)
                n_events_total = 0
                n_events_hit   = 0
                for d in test_data:
                    if d[0] == 'trailer':
                        n_events_total += 1
                        if d[1]['hits'] > 0:
                            n_events_hit += 1
                    if d[0] == 'data':
                        hit_matrix.fill(row=d[1]['row_id'], col=d[1]['col_id'])
                        hits_total[d[1]['row_id']][d[1]['col_id']] += 1
                        # NOTE could do some CRC check.

                print(f"Got number of total events {n_events_total=}")
                print(f"Events with at least one hit {n_events_hit=}")

                fig, ax = plt.subplots(1,1,figsize=(7,7))
                hit_matrix.plot2d(
                    ax=ax,
                )
                ax.set_ylabel(r'$Row$')
                ax.set_xlabel(r'$Column$')
                hep.cms.label(
                        "ETL Preliminary",
                        data=True,
                        lumi='0',
                        com=0,
                        loc=0,
                        ax=ax,
                        fontsize=15,
                    )
                name = 'hit_matrix_external_L1A'
                fig.savefig(os.path.join(plot_dir, "{}.pdf".format(name)))
                fig.savefig(os.path.join(plot_dir, "{}.png".format(name)))


                print("\nOccupancy vs column:")
                hit_matrix[{"row":sum}].show(columns=100)
                print("\nOccupancy vs row:")
                hit_matrix[{"col":sum}].show(columns=100)

        etroc.wr_reg("workMode", 0x0, broadcast=True)

        if args.scan == 'full':
            ### threshold scan draft
            vth_scan_data = vth_scan(
                etroc,
                vth_min = 400,
                vth_max = 500,
                decimal = True,
                fifo = fifo,
                absolute = True,
            )

            vth_axis    = np.array(vth_scan_data[0])
            hit_rate    = np.array(vth_scan_data[1])
            N_pix       = len(hit_rate) # total # of pixels
            N_pix_w     = int(round(np.sqrt(N_pix))) # N_pix in NxN layout
            max_indices = np.argmax(hit_rate, axis=1)
            maximums    = vth_axis[max_indices]
            max_matrix  = np.empty([N_pix_w, N_pix_w])
            noise_matrix  = np.empty([N_pix_w, N_pix_w])

            for pix in range(N_pix):
                r, c = fromPixNum(pix, N_pix_w)
                max_matrix[r][c] = maximums[pix]
                noise_matrix[r][c] = np.size(np.nonzero(hit_rate[pix]))
            # 2D histogram of the mean
            # this is based on the code for automatic sigmoid fits
            # for software emulator data below
            fig, ax = plt.subplots(2,1, figsize=(15,15))
            ax[0].set_title("Peak values of threshold scan")
            ax[1].set_title("Noise width of threshold scan")
            cax1 = ax[0].matshow(max_matrix)
            cax2 = ax[1].matshow(noise_matrix)
            fig.colorbar(cax1,ax=ax[0])
            fig.colorbar(cax2,ax=ax[1])
            
            ax[0].set_xticks(np.arange(N_pix_w))
            ax[0].set_yticks(np.arange(N_pix_w))

            ax[1].set_xticks(np.arange(N_pix_w))
            ax[1].set_yticks(np.arange(N_pix_w))
            
            for i in range(N_pix_w):
                for j in range(N_pix_w):
                    text = ax[0].text(j, i, int(max_matrix[i,j]),
                            ha="center", va="center", color="w", fontsize="xx-small")

                    text1 = ax[1].text(j, i, int(noise_matrix[i,j]),
                            ha="center", va="center", color="w", fontsize="xx-small")
                    
            #fig.savefig(f'results/peak_thresholds.png')
            fig.savefig(f'results/peak_and_noiseWidth_thresholds.png')
            plt.show()

            plt.close(fig)
            del fig, ax

        elif args.scan == 'simple':
            row = 4
            col = 3
            # FIXME the elink selector is still hard coded
            # but should not be
            # this also only works in the dual port mode right now
            # otherwise everything should be coming through link 2(?)
            if col > 7:
                fifo.select_elink(0)
            else:
                fifo.select_elink(2)
            rb_0.kcu.write_node("READOUT_BOARD_0.ERR_CNT_RESET", 1)
            print("\n - Running simple threshold scan on single pixel")
            vth     = []
            count   = []
            etroc.reset(hard=True)
            etroc.default_config()
            print("Coarse scan to find the peak location")
            for i in range(0, 1000, 5):
                # this could use a tqdm
                etroc.wr_reg("DAC", i, row=row, col=col)
                fifo.send_l1a(2000)
                vth.append(i)
                count.append(rb_0.kcu.read_node("READOUT_BOARD_0.DATA_CNT").value())
                print(i, rb_0.kcu.read_node("READOUT_BOARD_0.DATA_CNT").value())
                rb_0.kcu.write_node("READOUT_BOARD_0.ERR_CNT_RESET", 1)

            vth_a = np.array(vth)
            count_a = np.array(count)
            vth_max = vth_a[np.argmax(count_a)]
            print(f"Found maximum count at DAC setting vth_max={vth_max}")

            vth     = []
            count   = []
            print("Fine scanning around this DAC value now")
            for i in range(vth_max-15, vth_max+15):
                #etroc.wr_reg("DAC", i, row=3, col=4)
                etroc.wr_reg("DAC", i, row=row, col=col)
                fifo.send_l1a(5000)
                vth.append(i)
                count.append(rb_0.kcu.read_node("READOUT_BOARD_0.DATA_CNT").value())
                print(i, rb_0.kcu.read_node("READOUT_BOARD_0.DATA_CNT").value())
                rb_0.kcu.write_node("READOUT_BOARD_0.ERR_CNT_RESET", 1)

            print(vth)
            print(count)

            # FIXME add some plotting here

        if args.qinj_vth_scan:
            fifo.reset()
            q = 32
            delay = 3
            i = 4
            j = 3
            L1Adelay = 500
            print(f"\n - Will send L1a/QInj pulse with delay of {delay} cycles and charge of {q} fC")
            print(f"\n - to pixel at Row {i}, Col {j}.")
            #for dl in range(0,40,1):
            
            vth_axis    = np.linspace(400, 1023, 624)
            for vth in range(420,435,1):
                print(f"Working on threshold {vth=}")
                etroc.QInj_set(q, delay, L1Adelay, row=i, col=j, broadcast = False)
                etroc.wr_reg('DAC', int(vth), row=i, col=j, broadcast=False)
                #print("charge delay :", etroc.get_chargeInjDelay(), " L1A delay : ", etroc.get_L1Adelay(row=i, col=j))
                #for dl in range(0,700,10):
                #fifo.send_QInj(count=5000, delay=1)
                with tqdm(total=65536) as pbar:
                    while not fifo.is_full():
                        try:
                            kcu.write_node('READOUT_BOARD_0.L1A_QINJ_PULSE', 1)
                        except:
                            print('uhal._core.exception: Failed to pulse', file)
                #fifo.send_l1a(3200)
                #import pdb;pdb.set_trace();
                result = fifo.pretty_read(df)
                hits =0
                for word in result:
                    if(word[0] == 'data'):
                        #print(word)
                        hits+=1
                print("Total hits : ", hits)
                #import pdb;pdb.set_trace();
                etroc.QInj_unset(broadcast = True)
        if args.qinj:
            fifo.reset()
            q = 30
            delay = 3
            i = 4
            j = 3
            L1Adelay = 500
            print(f"\n - Will send L1a/QInj pulse with delay of {delay} cycles and charge of {q} fC")
            print(f"\n - to pixel at Row {i}, Col {j}.")
            for m in range(5):
                etroc.QInj_set(q, delay, L1Adelay, row=i, col=j, broadcast = False)
                with tqdm(total=65536) as pbar:
                    while not fifo.is_full():
                        try:
                            kcu.write_node('READOUT_BOARD_0.L1A_QINJ_PULSE', 1)
                        except:
                            print('uhal._core.exception: Failed to pulse', file)
                etroc.QInj_unset(broadcast = True)
                test_data = []
                while fifo.get_occupancy() > 0:
                    test_data += fifo.pretty_read(df)

                hits_total = np.zeros((16,16))
                hit_matrix = hist.Hist(col_axis,row_axis)
                n_events_total = 0
                n_events_hit   = 0
                for d in test_data:
                    if d[0] == 'trailer':
                        n_events_total += 1
                        if d[1]['hits'] > 0:
                            n_events_hit += 1
                    if d[0] == 'data':
                        hit_matrix.fill(row=d[1]['row_id'], col=d[1]['col_id'])
                        hits_total[d[1]['row_id']][d[1]['col_id']] += 1
                        # NOTE could do some CRC check.

                print(f"Got number of total events {n_events_total=}")
                print(f"Events with at least one hit {n_events_hit=}")

                fig, ax = plt.subplots(1,1,figsize=(7,7))
                hit_matrix.plot2d(
                    ax=ax,
                )
                ax.set_ylabel(r'$Row$')
                ax.set_xlabel(r'$Column$')
                hep.cms.label(
                        "ETL Preliminary",
                        data=True,
                        lumi='0',
                        com=0,
                        loc=0,
                        ax=ax,
                        fontsize=15,
                    )
                name = f'hit_matrix_external_L1A_QInj_Pulse_'+str(m)
                fig.savefig(os.path.join(plot_dir, "{}.pdf".format(name)))
                fig.savefig(os.path.join(plot_dir, "{}.png".format(name)))

                print("\nOccupancy vs column:")
                hit_matrix[{"row":sum}].show(columns=100)
                print("\nOccupancy vs row:")
                hit_matrix[{"col":sum}].show(columns=100)



    elif args.vth:
        # ==============================
        # ======= Test Vth scan ========
        # ==============================
        print("<--- Testing Vth scan --->")

        # run only if no saved data or we want to rerun
        if (not os.path.isfile("results/vth_scan.json")) or args.rerun:

            # scan
            print("No data. Run new vth scan...")
            result_data = vth_scan(ETROC2)

            #save
            if not os.path.isdir('results'):
                os.makedirs('results')

            with open("results/vth_scan.json", "w") as outfile:
                json.dump(result_data, outfile)
                print("Data saved to results/vth_scan.json\n")


        # read and parse vth scan data
        with open('results/vth_scan.json', 'r') as openfile:
            vth_scan_data = json.load(openfile)

        vth_axis = np.array(vth_scan_data[0])
        hit_rate = np.array(vth_scan_data[1])

        vth_min = vth_axis[0]  # vth scan range
        vth_max = vth_axis[-1]
        N_pix   = len(hit_rate) # total # of pixels
        N_pix_w = int(round(np.sqrt(N_pix))) # N_pix in NxN layout


        # ======= PERFORM FITS =======

        # fit to sigmoid and save to NxN layout
        slopes = np.empty([N_pix_w, N_pix_w])
        means  = np.empty([N_pix_w, N_pix_w])
        widths = np.empty([N_pix_w, N_pix_w])

        for pix in range(N_pix):
            fitresults = sigmoid_fit(vth_axis, hit_rate[pix])
            r, c = fromPixNum(pix, N_pix_w)
            slopes[r][c] = fitresults[0]
            means[r][c]  = fitresults[1]
            widths[r][c] = 4/fitresults[0]

        # print out results nicely
        for r in range(N_pix_w):
            for c in range(N_pix_w):
                pix = toPixNum(r, c, N_pix_w)
                print("{:8s}".format("#"+str(pix)), end='')
            print("")
            for c in range(N_pix_w):
                print("%4.2f"%means[r][c], end='  ')
            print("")
            for c in range(N_pix_w):
                print("+-%2.2f"%widths[r][c], end='  ')
            print("\n")


        # ======= PLOT RESULTS =======

        # fit results per pixel & save
        if args.fitplots:
            print('Creating plots and saving in ./results/...')
            print('This may take a while.')
            for expix in range(256):
                exr   = expix%N_pix_w
                exc   = int(np.floor(expix/N_pix_w))

                fig, ax = plt.subplots()

                plt.title("S curve fit example (pixel #%d)"%expix)
                plt.xlabel("Vth")
                plt.ylabel("hit rate")

                plt.plot(vth_axis, hit_rate[expix], '.-')
                fit_func = sigmoid(slopes[exr][exc], vth_axis, means[exr][exc])
                plt.plot(vth_axis, fit_func)
                plt.axvline(x=means[exr][exc], color='r', linestyle='--')
                plt.axvspan(means[exr][exc]-widths[exr][exc], means[exr][exc]+widths[exr][exc],
                            color='r', alpha=0.1)

                plt.xlim(vth_min, vth_max)
                plt.grid(True)
                plt.legend(["data","fit","baseline"])

                fig.savefig(f'results/pixel_{expix}.png')
                plt.close(fig)
                del fig, ax

        # 2D histogram of the mean
        fig, ax = plt.subplots()
        plt.title("Mean values of baseline voltage")
        cax = ax.matshow(means)

        fig.colorbar(cax)
        ax.set_xticks(np.arange(N_pix_w))
        ax.set_yticks(np.arange(N_pix_w))

        for i in range(N_pix_w):
            for j in range(N_pix_w):
                text = ax.text(j, i, "%.2f\n+/-%.2f"%(means[i,j],widths[i,j]),
                        ha="center", va="center", color="w", fontsize="xx-small")

        fig.savefig(f'results/sigmoid_mean_2D.png')
        plt.show()

        plt.close(fig)
        del fig, ax

        # 2D histogram of the width
        fig, ax = plt.subplots()
        plt.title("Width of the sigmoid")
        cax = ax.matshow(
            widths,
            cmap='RdYlGn_r',
            vmin=0, vmax=5,
        )

        fig.colorbar(cax)
        ax.set_xticks(np.arange(N_pix_w))
        ax.set_yticks(np.arange(N_pix_w))

        #cax.set_zlim(0, 10)

        fig.savefig(f'results/sigmoid_width_2D.png')
        plt.show()

    else:
        thresholds = [706-x*ETROC2.DAC_step for x in range(10)]
        print("Sending 10 L1As and reading back data, for the following thresholds:")
        print(thresholds)
        for th in thresholds:
            ETROC2.set_Vth_mV(th)  # anything between 196 and 203 should give reasonable numbers of hits
            print(f'Threshold at {ETROC2.get_Vth_mV(row=4, col=5)}mV')
            data = ETROC2.runL1A()  # this will spit out data for a single event, with an occupancy corresponding to the previously set threshold
            unpacked = [DF.read(d) for d in data]
            for d in data:
                print(DF.read(d))


        if unpacked[-1][1]['hits'] == len(unpacked)-2:
            print("Very simple check passed.")
            sys.exit(0)
        else:
            print("Data looks inconsistent.")
            sys.exit(1)
