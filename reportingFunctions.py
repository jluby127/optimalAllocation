import numpy as np
import matplotlib.pyplot as pt
import pandas as pd
import sys
import math
import time
import pickle
from collections import defaultdict
from astropy.time import Time
from astropy.time import TimeDelta
import astropy as apy
import astroplan as apl
import astropy.units as u
import plotly.graph_objects as go
import plotly.express as px
sys.path.append("/Users/jack/Desktop/")
sys.path.append("/Users/jack/Documents/Github/optimalAllocation/")
import helperFunctions as hf
import twilightFunctions as tw


def buildFullnessReport(allocation_schedule, twilightMap, combined_semester_schedule, nSlotsInQuarter, nSlotsInSemester, all_targets_frame, outputdir, STEP, round):
    file = open(outputdir + "runReport.txt", "a")
    file.write("Stats for " + str(round) + "\n")
    file.write("------------------------------------------------------" + "\n")
    # Compute how full the semester is
    nights_on_sky = []
    twilight_slots_on_sky_nights = 0
    for b in range(len(allocation_schedule)):
        if np.sum(allocation_schedule[b]) > 0:
            nights_on_sky.append(1)
            twilight_slots_on_sky_nights += ((np.sum(twilightMap[b])/4)*np.sum(allocation_schedule[b]))
        else:
            nights_on_sky.append(0)

    listnames = list(all_targets_frame['Starname'])
    slot_used_counter = 0
    for c in range(len(combined_semester_schedule)):
        for d in range(len(combined_semester_schedule[c])):
            if combined_semester_schedule[c][d] in listnames:
            #if combined_semester_schedule[c][d] != '' and combined_semester_schedule[c][d] != 'X' and combined_semester_schedule[c][d] != 'X*' and combined_semester_schedule[c][d] != '*' and combined_semester_schedule[n][s] != 'XW' and combined_semester_schedule[n][s] != 'X*W':
                slot_used_counter += 1

    total_available_slots = np.sum(np.array(allocation_schedule).flatten())*nSlotsInQuarter - twilight_slots_on_sky_nights
    file.write("N slots in semester:" + str(nSlotsInSemester) + "\n")
    file.write("N available slots: " + str(int(total_available_slots)) + "\n")
    file.write("N slots scheduled: " + str(slot_used_counter) + "\n")

    totalslotsrequested = 0
    for i in range(len(all_targets_frame)):
        totalslotsrequested += all_targets_frame['N_Unique_Nights_Per_Semester'][i]*math.ceil(all_targets_frame['Exposure_Time'][i]/(STEP*60.))
    file.write("N slots requested (total): " + str(totalslotsrequested) + "\n")
    percentage = np.round((slot_used_counter*100)/total_available_slots,3) # round((slot_used_counter*100)/total_available_slots,3)
    file.write("Percent full: " + str(percentage) + "%." + "\n")
    file.close()

    ff = open(outputdir + "semester_schedule.txt", "w")
    for ind in range(len(combined_semester_schedule)):
        ff.write("This is day " + str(ind) + "\n")
        ff.write("Q1: " + str(combined_semester_schedule[ind][nSlotsInQuarter*0:nSlotsInQuarter*1]) + "\n")
        ff.write("Q2: " + str(combined_semester_schedule[ind][nSlotsInQuarter*1:nSlotsInQuarter*2]) + "\n")
        ff.write("Q3: " + str(combined_semester_schedule[ind][nSlotsInQuarter*2:nSlotsInQuarter*3]) + "\n")
        ff.write("Q4: " + str(combined_semester_schedule[ind][nSlotsInQuarter*3:nSlotsInQuarter*4]) + "\n")
        ff.write("--------------------------------------------------------------------------------------" + "\n")
    ff.close()


def buildCOF(outputdir, current_day, all_targets_frame, all_dates_dict, combined_semester_schedule, dates_in_semester):
    x = []
    y = []
    prog = []
    totobs = []
    commentsfile = open(outputdir + "ProgramData.csv", 'w')

    for program in all_targets_frame['Program_Code'].unique():

        programMask = all_targets_frame['Program_Code'] == program
        programDict = all_targets_frame[programMask]
        programDict.reset_index(inplace=True)
        if program == 'S001':
            tot_obs = len(programDict)*extra
        else:
            tot_obs = np.sum(programDict['N_Unique_Nights_Per_Semester'])

        runningObsList = [0.]*len(all_dates_dict) #*nNightsInSemester #
        runval = 0

        for targ in programDict['Starname']:
            for day in range(len(runningObsList)):
                if targ in combined_semester_schedule[day]:
                    runningObsList[day] += 1

        newrunning = 0.
        for e in range(len(runningObsList)):
            x.append(dates_in_semester[e])
            newrunning += runningObsList[e]
            y.append(round((newrunning/tot_obs)*100,2))
            prog.append(program)
            totobs.append(tot_obs)

        commentsfile.write('#' + str(program) + '_trueComplete:' + str(round(y[-1],2)) + '\n')

    programdata = pd.DataFrame({"Program":prog, "Date":x, "Percent Complete (Observations)":y, "Total Obs Requested":totobs})

    fig = px.line(programdata, x="Date", y="Percent Complete (Observations)", hover_data=['Total Obs Requested'],
                color='Program',title='Cumulative Observation Function - N_Obs')

    fig.add_vrect(
            x0=current_day,
            x1=current_day,
            annotation_text="Today",
            line_dash="dash",
            fillcolor=None,
            line_width=2,
            line_color='black',
            annotation_position="bottom left"
        )
    fig.write_html(outputdir + "/COF_Nobs_" + str(current_day) + ".html")



def buildAllocationPicture(allocation_schedule, nNightsInSemester, nQuartersInNight, startingNight, all_dates_dict, outputdir):
    dateslist = list(all_dates_dict.keys())
    ff = open(outputdir + "AllocationPicture_stats.txt", "w")

    fig = pt.figure(figsize=(12,5))
    q1s = 0
    q2s = 0
    q3s = 0
    q4s = 0
    count0 = 0
    count1 = 0
    count2 = 0
    count3 = 0
    count4 = 0
    for j in range(len(allocation_schedule)):
        if allocation_schedule[j][0] == 1.:
            q1s += 1
            pt.axvline(startingNight + j, ymin=0., ymax=0.25, color='b')
            date_info = dateslist[startingNight + j] + " - q0"
            ff.write(date_info + "\n")

        if allocation_schedule[j][1] == 1.:
            q2s += 1
            pt.axvline(startingNight + j, ymin=0.25, ymax=0.5, color='b')
            date_info = dateslist[startingNight + j] + " - q1"
            ff.write(date_info + "\n")

        if allocation_schedule[j][2] == 1.:
            q3s += 1
            pt.axvline(startingNight + j, ymin=0.5, ymax=0.75, color='b')
            date_info = dateslist[startingNight + j] + " - q2"
            ff.write(date_info + "\n")

        if allocation_schedule[j][3] == 1.:
            q4s += 1
            pt.axvline(startingNight + j, ymin=0.75, ymax=1.0, color='b')
            date_info = dateslist[startingNight + j] + " - q3"
            ff.write(date_info + "\n")


        allocated_quarters = np.sum(allocation_schedule[j])
        if allocated_quarters == 0:
            count0 += 1
        if allocated_quarters == 1:
            count1 += 1
        if allocated_quarters == 2:
            count2 += 1
        if allocated_quarters == 3:
            count3 += 1
        if allocated_quarters == 4:
            count4 += 1

    size=15
    pt.xlabel('Day')
    pt.ylabel('Quarter')
    pt.xlim(-5, 188)
    pt.yticks(range(4), ['Q1', 'Q2', 'Q3', 'Q4'])
    pt.ylim(0,4)
    pt.axhline(1, color='k', linestyle='-')
    pt.axhline(2, color='k', linestyle='-')
    pt.axhline(3, color='k', linestyle='-')
    pt.axhline(4, color='k', linestyle='-')
    pt.savefig(outputdir + "AllocationPicture.png", dpi=300, bbox_inches='tight', facecolor='w')

    ff.write("\n")
    ff.write("\n")
    ff.write("\n")
    ff.write("\n")
    ff.write("There are " + str(q1s) + " first quarters." + "\n")
    ff.write("There are " + str(q2s) + " second quarters." + "\n")
    ff.write("There are " + str(q3s) + " third quarters." + "\n")
    ff.write("There are " + str(q4s) + " fourth quarters." + "\n")
    ff.write("\n")
    ff.write("There are " + str(count0) + " no quarter nights." + "\n")
    ff.write("There are " + str(count1) + " 1 quarter nights."+ "\n")
    ff.write("There are " + str(count2) + " 2 quarter nights."+ "\n")
    ff.write("There are " + str(count3) + " 3 quarter nights."+ "\n")
    ff.write("There are " + str(count4) + " 4 quarter nights."+ "\n")
    ff.write("\n")
    total_quarters = count1 + 2*count2 + 3*count3 + 4*count4
    total_nights = count1 + count2 + count3 + count4
    ff.write("Total quarters allocated: " + str(total_quarters) + "\n")
    ff.write("Total unique nights allocated: " + str(total_nights) + "\n")
    ff.close()


def buildBinaryAllocationMap(outputdir, allocation_schedule):
    # Build the allocation map for the auto-scheduler
    # Example: run this to create a map of the results of optimal allocaiton
    # that then can be used to run the semester scheduler algorithm
    filename = outputdir + 'testing_Binary_Schedule.txt'
    file = open(filename, 'w')
    for a in range(len(allocation_schedule)):
        line = all_dates[a] + " : " + str(allocation_schedule[a])[1:-1]
        file.write(str(line) + "\n")
    file.close()
