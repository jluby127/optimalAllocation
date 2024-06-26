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


def buildHumanReadableSchedule(Yns, twilightMap, all_targets_frame, nNightsInSemester, nSlotsInNight, AvailableSlotsInGivenNight, nSlotsInQuarter, all_dates_dict, current_day, allocation_map_NS, weathered_map, slotsNeededDict):

    # retrieve the first order semester schedule
    semester_schedule = []
    fullslots = 0
    for v in Yns.values():
        if np.round(v.X,0) == 1:
            name = v.VarName[19:][:-1].split(',')[0]
            semester_schedule.append(name)
            fullslots += 1
        else:
            semester_schedule.append("")
    semester_schedule = np.reshape(semester_schedule, (len(all_targets_frame), nNightsInSemester, nSlotsInNight))

    # build the combined schedule, which displays info of allocated vs non-allocated slots, including twilight and weather
    # combined_semester_schedule = np.empty((nNightsInSemester,nSlotsInNight), dtype=object)
    combined_semester_schedule = np.empty((len(all_dates_dict),nSlotsInNight), dtype=object)
    for c in range(all_dates_dict[current_day]):
        for d in range(len(combined_semester_schedule[c])):
            combined_semester_schedule[c][d] = 'Past'

    for n in range(nNightsInSemester):
        for s in range(nSlotsInNight):
            slotallocated = ''
            if allocation_map_NS[n][s] == 0:
                slotallocated += 'X'
            if twilightMap[n][s] == 1:
                slotallocated += '*'
            if weathered_map[n][s] == 1:
                slotallocated += 'W'
            for t in range(len(all_targets_frame)):
                slotallocated += semester_schedule[t][n][s]
            combined_semester_schedule[n+all_dates_dict[current_day]][s] = str(slotallocated)

    listnames = list(all_targets_frame['Starname'])
    # fill in the "unused" slots...those that were held empty because a target needed multiple slots. Fill in those accordingly
    for n in range(nNightsInSemester-1, -1, -1):
        for s in range(nSlotsInNight-1, -1, -1):
            if combined_semester_schedule[n+all_dates_dict[current_day]][s] in listnames:
            #if combined_semester_schedule[n][s] != '' and combined_semester_schedule[n][s] != 'X' and combined_semester_schedule[n][s] != '*' and combined_semester_schedule[n][s] != 'X*' and combined_semester_schedule[n][s] != 'XW' and combined_semester_schedule[n][s] != 'X*W':
                target_name = combined_semester_schedule[n+all_dates_dict[current_day]][s]
                slotsneededperExposure = slotsNeededDict[target_name]
                if slotsneededperExposure > 1:
                    for e in range(1, slotsneededperExposure):
                        combined_semester_schedule[n+all_dates_dict[current_day]][s+e] += target_name

    return combined_semester_schedule


def getSemesterInfo(current_day):
    if current_day[5:7] in ['02', '03', '04', '05', '06', '07']:
        semesterLetter = 'A'
    elif current_day[5:7] in ['08', '09', '10', '11', '12', '01']:
        semesterLetter = 'B'
    else:
        print("invalid date")
    semesterYear = current_day[:4]

    if semesterLetter == 'A':
        semester_start_date = semesterYear + '-02-01'
        # check if this is a leap year
        if int(semesterYear) in np.arange(2024, 2128, 4):
            # Note from Jack Lubin in the year 2024: in the year 2128 you'll have to update this line for another 200 years.
            # The new line should be: np.arange(2128, 2228, 4)
            semesterLength = 182
        else:
            semesterLength = 181
    elif semesterLetter == 'B':
        semester_start_date = semesterYear + '-08-01'
        semesterLength = 184
    return semester_start_date, semesterLength


def buildDayDateDictionary(semester_start_date, semesterLength):
    all_dates = {}
    date_formal = Time(semester_start_date, format='iso',scale='utc')
    date = str(date_formal)[:10]
    all_dates[date] = 0
    for i in range(1, semesterLength):
        date_formal += TimeDelta(1,format='jd')
        date = str(date_formal)[:10]
        all_dates[date] = i
    return all_dates


# This computes how many days in the semester have already gone by and updates the nNightsInSemester parameter
def currentDayTracker(current_day, all_dates):
    remove_days = all_dates[current_day] - 1
    return len(all_dates) - 1 - remove_days


# This builds a map of the slots that are not allocated
def buildNonAllocatedMap(allocation_schedule, weatherDiff, AvailableSlotsInGivenNight, nSlotsInSemester, nNightsInSemester, nQuartersInNight, nSlotsInQuarter, nSlotsInNight):
    allocation_map = [0]*nSlotsInSemester
    allocation_map_weathered = [0]*nSlotsInSemester
    for n in range(nNightsInSemester):
        for q in range(nQuartersInNight):
            allocated = allocation_schedule[n][q]
            if allocated == 1:
                start = n*nSlotsInQuarter*nQuartersInNight + q*nSlotsInQuarter
                end  = start + nSlotsInQuarter - (nSlotsInQuarter - int(AvailableSlotsInGivenNight[n]/nQuartersInNight) - 1)
                for s in range(start, end):
                    allocation_map[s] += 1
            weathered = weatherDiff[n][q]
            if weathered == 1:
                start = n*nSlotsInQuarter*nQuartersInNight + q*nSlotsInQuarter
                end  = start + nSlotsInQuarter - (nSlotsInQuarter - int(AvailableSlotsInGivenNight[n]/nQuartersInNight) - 1)
                for s in range(start, end):
                    allocation_map_weathered[s] += 1
    #The NS stands for Night Slot, so this version is made to be 2D whereas the allocation_map itself is a 1D list
    allocation_map_NS = np.reshape(allocation_map, (nNightsInSemester, nSlotsInNight))
    allocation_map_weathered_NS = np.reshape(allocation_map_weathered, (nNightsInSemester, nSlotsInNight))
    return allocation_map, allocation_map_NS, allocation_map_weathered_NS


def buildTwilightMap(windowsPerNight, nSlotsInQuarter):

    nightly_twilight_map = []
    for i in range(len(windowsPerNight)):

        quarterslots = [0]*nSlotsInQuarter
        for j in range(nSlotsInQuarter):

#             # due to large/quantized slot sizes, some "leftover" twilight time might get evenly spread across the 4
#             # quarters of the night. But we don't want that. If any twilight time is left over, then all that is
#             # spread across the quarters should be "rounded up" so that one extra slot is not available for targets.
#             # this is merely encoding into the human readable schedule what the algorith is implicity doing.
#             # So that we don't penalize ourselves for not filling all slots that the schedule shows are available, when
#             # in reality they are not available.

#             if windowsPerNight[i]%4 == 1:
#                 extra = 1
#             elif windowsPerNight[i]%4 == 3:
#                 extra = 0
#             else:
#                 extra = 0
#             # end logic here and resume normal operations

            extra = 0
            if j > int(windowsPerNight[i]/4) - extra:
                quarterslots[j] = 1
        quarterslots.extend(quarterslots)
        # second 'extend' is doubling the already doubled length
        quarterslots.extend(quarterslots)
        nightly_twilight_map.append(quarterslots)
    return nightly_twilight_map

def buildEnforcedDates(filename, all_dates_dict):
    enforced_dates = []
    selections = pd.read_csv(filename)
    for s in range(len(selections)):
        night = all_dates_dict[selections['date'][s]]
        pair = [night, selections['quarter'][s]]
        enforced_dates.append(pair)
    return enforced_dates
