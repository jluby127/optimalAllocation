import numpy as np
import matplotlib.pyplot as pt
import pandas as pd
import sys
import math
import time
import pickle
from collections import defaultdict
import os
import warnings
warnings.filterwarnings('ignore')

from astropy.time import Time
from astropy.time import TimeDelta
import astropy as apy
import astroplan as apl
import astropy.units as u

import gurobipy as gp
from gurobipy import GRB

# KPF-CC specific files
dirpath = '/Users/jack/Documents/Github/optimalAllocation/'
sys.path.append(dirpath)
import helperFunctions as hf
import twilightFunctions as tw
import reportingFunctions as rf
import processingFunctions as pf
import mappingFunctions as mf


def runKPFCCv2(current_day, request_sheet, allocated_nights, accessmapfile, twilight_times,
                           outputdir, STEP, runOptimalAllocation, runRound2,
                           pastDatabase, starmap_template_filename, turnFile,
                           enforcedNO_file, enforcedYES_file, nonqueueMap, nonqueueMap_str, nonqueueObs_info,
                           gurobi_output, plot_results, SolveTime):

    # set a few parameters and flags
    # if computing optimal allocation, don't run extra rounds and extend time limit
    if runOptimalAllocation:
        if current_day != ['2024-08-01']:
            current_day = ['2024-08-01']
        runRound2 = False

    # I suggest your output directory be something so that it doesn't autosave
    # to the same directory as the run files and crowds up the GitHub repo.
    check = os.path.isdir(outputdir)
    if not check:
        os.makedirs(outputdir)
    # this represents the day of the semester we are scheduling
    # for now this parameter only sets the number of days left in the semester,
    # and therefore the number of slots in the semester
    # and therefore the size of the Yns, Wns, etc variables.
    # For testing CPU usage, always set this to '2024-02-01' as this
    # was the first day of the 2024A semester.
    current_day = current_day[0]

    start_all = time.time()

    # -------------------------------------------------------------------
    # -------------------------------------------------------------------
    #               Set up logistics parameters
    # -------------------------------------------------------------------
    # -------------------------------------------------------------------
    semester_start_date, semester_end_date, semesterLength, semesterYear, semesterLetter = hf.getSemesterInfo(current_day)
    all_dates_dict = hf.buildDayDateDictionary(semester_start_date, semesterLength)
    dates_in_semester = list(all_dates_dict.keys())
    nNightsInSemester = hf.currentDayTracker(current_day, all_dates_dict)

    nQuartersInNight = 4
    nHoursInNight = 14
    nSlotsInQuarter = int(((nHoursInNight*60)/nQuartersInNight)/STEP)
    nSlotsInNight = nSlotsInQuarter*nQuartersInNight
    nSlotsInSemester = nSlotsInNight*nNightsInSemester
    semester_grid = np.arange(0,nNightsInSemester,1)
    quarters_grid = np.arange(0,nQuartersInNight,1)
    semesterSlots_grid = np.arange(0,nSlotsInSemester,1)
    nightSlots_grid = np.arange(0,nSlotsInNight,1)
    startingSlot = all_dates_dict[current_day]*nSlotsInNight
    startingNight =  all_dates_dict[current_day]

    # -------------------------------------------------------------------
    # -------------------------------------------------------------------
    #               Read in files and prep targets
    # -------------------------------------------------------------------
    # -------------------------------------------------------------------
    print("Reading in and prepping files")

    # Read in and prep target info
    all_targets_frame = pd.read_csv(request_sheet)

    # build dictionary where keys are target names and
    # values are how many slots are needed to complete one exposure for that target
    slotsNeededDict = {}
    for n,row in all_targets_frame.iterrows():
        name = row['Starname']
        exptime = row['Nominal_ExpTime']
        slotsneededperExposure = math.ceil(exptime/(STEP*60.))
        slotsNeededDict[name] = slotsneededperExposure
        #if slotsneededperExposure > 1:
        #    print(name, slotsneededperExposure)

    # sub-divide target list into only those that are more than 1 unique night of observations
    # later, single shot observations are scheduled in Round 3
    cadenced_targets = all_targets_frame[(all_targets_frame['N_Unique_Nights_Per_Semester'] > 1)]
    cadenced_targets.reset_index(inplace=True)
    ntargets = len(cadenced_targets)

    # Read in twilight times
    twilight_frame = pd.read_csv(twilight_times, parse_dates=True, index_col=0)

    # AvailableSlotsInGivenNight is a 1D matrix of length nights n
    # This is not a gorubi variable, but a regular python variable
    # Each element will hold an integer which represents the true number of STEP minute slots
    # in a quarter in a given night, after accounting for non-observable times due to day/twilight.
    AvailableSlotsInGivenNight = []
    for date in dates_in_semester:
        edge = tw.determineTwilightEdges(date, twilight_frame, STEP, verbose=False)
        AvailableSlotsInGivenNight.append(edge)

    # Build the twilight times maps
    twilightMap_all = np.array(mf.buildTwilightMap(AvailableSlotsInGivenNight, nSlotsInNight, invert=False, reorder=False))

    twilightMap_toDate = twilightMap_all.copy()
    twilightMap_toDate = twilightMap_toDate[all_dates_dict[current_day]:]

    twilightMap_all_flat = twilightMap_all.copy()
    twilightMap_all_flat = twilightMap_all_flat.flatten()

    twilightMap_toDate_flat = twilightMap_toDate.copy()
    twilightMap_toDate_flat = twilightMap_toDate_flat.flatten()

    # Read in serialized pre-computed accessibility maps and deserialize it
    rewriteFlag = False
    accessmaps_precompute = mf.readAccessibilityMapsDict(accessmapfile)
    for name in all_targets_frame['Starname']:
        # check that this target has a precomputed accessibility map, if not, make one and add it to the pickle file
        try:
            tmpRead = accessmaps_precompute[name]
        except:
            print(name + " not found in precomputed accessibilty maps. Running now.")
            newmap = mf.singleTargetAccessible(starname, ra, dec, startdate, nNightsInSemester)
            accessmaps_precompute[name] = newmap
            rewriteFlag = True
    if rewriteFlag:
        mf.writeAccessibilityMapsDict(accessmaps_precompute, '/Users/jack/Desktop/tmpaccessmap.pkl') #accessmapfile)

    # set different constraints depending on if we are running the
    # optimal allocation algorithm or a regular semester schedule solver
    pastObs_Info = {}
    if runOptimalAllocation == False:
        print("Solve the regular semester schedule.")
        # Read in allocation info
        # Then a simple conversion from human-readable to computer-readable
        # I wanted to keep the data in the Binary Schedule for easy manual editing if needed
        allocation_schedule_load = np.loadtxt(allocated_nights, dtype=str)
        allocation_schedule_full = []
        for a in range(all_dates_dict[current_day], len(allocation_schedule_load)):# - all_dates_dict[current_day]): #maybe revert back without the minus
            convert = list(map(int, allocation_schedule_load[a][2:]))
            allocation_schedule_full.append(convert)

        allocation_schedule_full_semester = []
        for b in range(len(allocation_schedule_load)):
            convert = list(map(int, allocation_schedule_load[b][2:]))
            allocation_schedule_full_semester.append(convert)

        print("Sampling out weather losses")
        # Randomly sample out 30% of future allocated quarters to simulate weather loss
        allocation_schedule_long = np.array(allocation_schedule_full).flatten()
        # These are the non-queue nights/quarters. Do not allow them to be weathered out.
        protectedQuarters = []
        if nonqueueObs_info != 'nofilename.csv':
            protectedWeather = pd.read_csv(nonqueueObs_info)
            for b in range(len(protectedWeather)):
                night = (all_dates_dict[protectedWeather['Date'][b]] - all_dates_dict[current_day])*4
                quartlist = list(protectedWeather['Quarters'][b])
                quarts = []
                for l in range(len(quartlist)):
                    try:
                        val = int(quartlist[l])
                        quarts.append(val)
                    except:
                        continue
                for q in range(len(quarts)):
                    protectedQuarters.append(night + q)
        allindices = [i for i in range(len(allocation_schedule_long)) if allocation_schedule_long[i] == 1 and i not in protectedQuarters]
        weatherlosses = np.random.choice(allindices, int(0.3*len(allindices)), replace=False)
        for w in weatherlosses:
            allocation_schedule_long[w] = 0
        allocation_schedule = np.reshape(allocation_schedule_long, (nNightsInSemester, 4))
        weatherDiff = allocation_schedule_full - allocation_schedule

        # Build the allocation map
        allocation_map, allocation_map_NS, weathered_map = mf.buildAllocationMap(allocation_schedule, weatherDiff, AvailableSlotsInGivenNight, twilightMap_toDate)
        allocation_map_fullsemester, allocation_map_NS_fullsemester, weathered_map_ignorethis = mf.buildAllocationMap(allocation_schedule_full_semester, np.zeros(np.shape(allocation_schedule_full_semester)), AvailableSlotsInGivenNight, twilightMap_all)

        # create the intersection of the two
        alloAndTwiMap = allocation_map&twilightMap_toDate_flat

        # Pull the database of past observations and process.
        # For each target, determine the most recent date of observations and the number of unique days observed.
        # Also process the past to build the starmap for each target.
        if pastDatabase != 'nofilename.csv':
            database = pd.read_csv(pastDatabase)
            for i in range(len(all_targets_frame['Starname'])):
                starmask = database['star_id'] == all_targets_frame['Starname'][i]
                star_past_obs = database[starmask]
                star_past_obs.sort_values(by='utctime', inplace=True)
                star_past_obs.reset_index(inplace=True)

                total_past_observations = len(star_past_obs)
                #total_open_shutter_time = np.sum(star_past_obs['Nominal_ExpTime'])
                star_past_obs, unique_hstdates_observed, quarterObserved = pf.getUniqueNights(star_past_obs, twilight_frame)
                if len(unique_hstdates_observed) > 0:
                    mostRecentObservationDate = unique_hstdates_observed[-1]
                else:
                    mostRecentObservationDate = '0000-00-00'
                Nobs_on_date = pf.getNobs_on_Night(star_past_obs, unique_hstdates_observed)

                # within the pastObs_Info dictionary, each target's data is always in the given order:
                # element 0 = the calendar date of the most recent observation (HST date)
                # element 1 = the list of calendar dates with at least one observation
                # element 2 = the list of the quarter where the observation took place on the corresponding unique night from element 1's list (if multi visits, then this is for the first visit)
                # element 3 = the list of the number of observations that took place on the corresponding unique night from element 1's list
                pastObs_Info[all_targets_frame['Starname'][i]] = [mostRecentObservationDate, unique_hstdates_observed, quarterObserved, Nobs_on_date]
                # print("STARNAME: ", all_targets_frame['Starname'][i])
                # print("Most Recent Observation: ", mostRecentObservationDate)
                # print('Unique obs dates: ', unique_hstdates_observed)
                # print('Quarter observed: ', quarterObserved)
                # print("nobs on date: ", Nobs_on_date)
                # print()
                # print(all_targets_frame['N_Observations_per_Visit'][i] * all_targets_frame['N_Visits_per_Night'][i])
                # print(all_targets_frame['N_Unique_Nights_Per_Semester'][i])
                # print(len(unique_hstdates_observed))
                # print(all_targets_frame['N_Unique_Nights_Per_Semester'][i] - len(unique_hstdates_observed))
                # print("---------------------------------------")
                # print()

    # -------------------------------------------------------------------
    # -------------------------------------------------------------------
    #               Set Gorubi model variables
    # -------------------------------------------------------------------
    # -------------------------------------------------------------------
    print("Building variables")

    m = gp.Model('Semester_Scheduler')

    # Yns (ExposureStartSlot) is a 2D matrix of targets n and slots s
    # Slot s for target n will be 1 to indicate starting an exposure
    Yns = m.addVars(all_targets_frame['Starname'], semesterSlots_grid, vtype = GRB.BINARY, name = 'ExposureStart_Slot')

    # Wnd (ExposureStartNight) is a 2D matrix of targets t and nights d
    # Night d for target n will be 1 to indicate this target gets an exposure on this night
    # useful for tracking cadence and building the DeltaDays variable
    Wnd = m.addVars(all_targets_frame['Starname'], semesterSlots_grid, vtype = GRB.BINARY, name = 'ExposureStart_Night')

    # Thn (Theta_n) is the "shortfall" variable
    Thn = m.addVars(all_targets_frame['Starname'], name='Theta_n')

    if runOptimalAllocation == True:
        # Anq is a 2D matrix of N_nights_in_semester by N_quarters_in_night
        # element will be 1 if that night/quarter is allocated to KPF and 0 otherwise
        Anq = m.addVars(semester_grid, quarters_grid, vtype = GRB.BINARY, name = 'Allocation_Map')

        # Un is a 1D matrix of N_nights_in_semester
        # element will be 1 if at least one quarter in that night is allocated
        Un = m.addVars(semester_grid, vtype = GRB.BINARY, name = 'UniqueAllocation_Map')
        m.update()


    # -------------------------------------------------------------------
    # -------------------------------------------------------------------
    #               Set Gorubi constraints
    # -------------------------------------------------------------------
    # -------------------------------------------------------------------

    print("Constraint: relating Yns to Wns")
    # Relate Yns to Wns
    # For target n, only one slot within the night can be set to 1 (one exposure per night)
    # For target n, if any slot s within night d is 1, then Wnd must be 1
    for d in range(nNightsInSemester):
        start = d*nSlotsInNight
        end = start + nSlotsInNight
        for name in all_targets_frame['Starname']:
            m.addConstr((gp.quicksum(Yns[name,s] for s in range(start, end)) <= 1), 'oneObsPerNight_' + str(name) + "_" + str(d) + "d")
            for s in range(start, end):
                m.addConstr(Yns[name, s] <=  Wnd[name, d], "related_Yns_Wnd_" + str(name) + "_" + str(d) + "_" + str(s))

    print("Constraint: no other exposures can start in multi-slot exposures")
    # Enforce no other exposures start within t slots if Yns[name, slot] is 1
    # Velibor's new logic
    for n,row in all_targets_frame.iterrows():
        name = row['Starname']
        slotsneededperExposure = slotsNeededDict[name]
        if (slotsneededperExposure > 1):
            for s in range(nSlotsInSemester):
                m.addConstr((slotsneededperExposure-1)*Yns[name,s] <= ( (slotsneededperExposure - 1) - gp.quicksum(Yns[name2,s+t] for name2 in all_targets_frame['Starname'] for t in range(1, min(slotsneededperExposure, nSlotsInSemester - s)))), 'dont_Start_Exposure_' + str(name) + "_" + str(s) + "s")

    print("Constraint: only 1 exposure per slot")
    # Enforce only one exposure per slot
    for s in range(nSlotsInSemester):
        m.addConstr(gp.quicksum(Yns[name,s] for name in all_targets_frame['Starname']) <= 1, 'ensure_singleOb_perSlot_' + str(s) + "s")

    print("Constraint: exposure can't start if it won't complete in the night/quarter.")
    # Example: a full night allocation where the 2nd quarter of the night is "weathered". In this scenario,
    # This constraint must be applied at both the end of the 1st quarter and the end of the 4th quarter.
    # But this constraint is not applied to the 3rd quarter: an exposure can span the boundary of 3 to 4 quarter.
    # Enforce that an exposure cannot start if it will not complete within the same night/quarter.
    if runOptimalAllocation == False:
        for d in range(nNightsInSemester):
            # end_night_slot = (d+1)*nSlotsInNight - (nSlotsInNight - int(AvailableSlotsInGivenNight[d]/2)) - 1 # the -1 is to account for python indexing
            for s in range(nSlotsInNight - 1): # -1 so that we don't hit the end of the loop. Plus the last and second to last slot should always be allocated as 0 so no need to test.
                if allocation_map_NS[d][s] == 1 and allocation_map_NS[d][s+1] == 0:
                    for name in all_targets_frame['Starname']:
                        if slotsNeededDict[name] > 1:
                            for e in range(0, slotsNeededDict[name]-1): # the -1 is so because a target can be started if it just barely fits
                                m.addConstr(Yns[name,s-e] == 0, 'dont_start_near_endof_night_' + name + "_" + str(s) + 's_' + str(e) + 'e')

    print("Constraint: inter-night cadence of future observations.")
    # Constrain the future inter-night cadence
    counter = 0
    for d in range(nNightsInSemester):
        for t,row in all_targets_frame.iterrows():
            name = row['Starname']
            internightcadence = int(row['Inter_Night_Cadence'])
            for dlta in range(1, internightcadence):
                try:
                    m.addConstr(Wnd[name,d] <= 1 - Wnd[name,d+dlta], 'enforce_internightCadence_' + str(name) + "_" + str(d) + "d_" + str(dlta) + "dlta")
                except:
                    # enter this except when the inter-night cadence brings us beyond the end of the semester (i think)
                    counter += 1
                    continue

    # print("Constraint: multi-visit targets can only be attempted under certain conditions.")
    # # Constrain the nights when a multi-visit target can be scheduled
    # for t,row in all_targets_frame.iterrows():
    #     name = row['Starname']
    #     visits = int(row['N_Visits_per_Night'])
    #     if visits > 1:
    #         newexptime = (visits-1)*200 + row['N_Observations_per_Visit']*45 + row['N_Observations_per_Visit']*row['Nominal_ExpTime']
    #         new_nslots = math.ceil(newexptime/(STEP*60.))
    #         for d in range(nNightsInSemester):
    #             alloAndTwiMap[d]



    print("Constraint: build Theta variable")
    # Construct the Theta_n variable
    extra = 20
    for t,row in all_targets_frame.iterrows():
        name = row['Starname']
        Nobs_Unique = row['N_Unique_Nights_Per_Semester']
        if pastObs_Info == {}:
            past_Unique = 0
        else:
            past_Unique = len(pastObs_Info[name][1])
        m.addConstr(Thn[name] >= 0, 'ensureGreaterThanZero_' + str(name))
        # normalized theta value
        # m.addConstr(Thn[name] >= (Nobs_Unique - gp.quicksum(Yns[name, s] for s in range(nSlotsInSemester)))/Nobs_Unique, 'ensureGreaterThanNobsShortfall_' + str(name))
        # unnormalized theta value
        m.addConstr(Thn[name] >= ((Nobs_Unique - past_Unique) - gp.quicksum(Yns[name, s] for s in range(nSlotsInSemester))), 'ensureGreaterThanNobsShortfall_' + str(name))
        # add an upper limit to how many extra obs can be scheduled for a single target
        m.addConstr((gp.quicksum(Wnd[name,d] for d in range(nNightsInSemester)) <= (Nobs_Unique - past_Unique) + extra), 'max_Nobs_Unique_Nights_' + str(name))

    if runOptimalAllocation:
        print("Add the optimal allocation constraints.")

        print("Constraint: enforce no observations when in daylight.")
        print("Constraint: enforce no observations when target not accessible.")
        for name in all_targets_frame['Starname']:
            access = np.array(accessmaps_precompute[name]).flatten()
            fullmap = twilightMap_all_flat&access
            for s in range(nSlotsInSemester):
                m.addConstr(Yns[name,s] <= fullmap[s], 'enforceMaps_' + str(name) + "_" + str(s) + "s")

        # Constraints on the way the allocation map can be filled
        # these can and should be edited on a semester by semester basis
        maxQuarters = 126 #86
        maxNights = 60
        minQuarterSelection = 5

        print("Constraint: setting max number of quarters allocated.")
        # No more than a maximum number of quarters can be allocated
        m.addConstr(gp.quicksum(Anq[d,q] for d in range(nNightsInSemester) for q in range(nQuartersInNight)) <= maxQuarters, "maximumQuartersAllocated")

        print("Constraint: relating allocation map and unique night allocation map.")
        # relate unique_allocation and allocation
        # if any one of the q in allocation[given date, q] is 1, then unique_allocation[given date] must be 1, zero otherwise
        for d in range(nNightsInSemester):
            for q in range(nQuartersInNight):
                m.addConstr(Un[d] >= Anq[d,q], "relatedUnique_andNonUnique_lowerbound_" + str(d) + "d_" + str(q) + "q")
            m.addConstr(Un[d] <= gp.quicksum(Anq[d,q] for q in range(nQuartersInNight)), "relatedUnique_andNonUnique_upperbound_" + str(d) + "d")

        print("Constraint: cannot observe if night/quarter is not allocated.")
        # if quarter is not allocated, all slots in quarter must be zero
        for s in range(nSlotsInSemester):
            for name in all_targets_frame['Starname']:
                d = int(s/nSlotsInNight)
                q = int((s%nSlotsInNight)/nSlotsInQuarter)
                m.addConstr(Yns[name, s] <= Anq[d, q], "dontSched_ifNot_Allocated_"+ str(d) + "d_" + str(q) + "q_" + str(s) + "s_" + name)

        print("Constraint: setting max number of unique nights allocated.")
        # No more than a maximum number of unique nights can be allocated
        m.addConstr(gp.quicksum(Un[d] for d in range(nNightsInSemester)) <= maxNights, "maximumNightsAllocated")

        print("Constraint: setting min number each quarter to be allocated.")
        # Minimum number of each quarter must be allocated
        m.addConstr(gp.quicksum(Anq[d,0] for d in range(nNightsInSemester)) >= minQuarterSelection, "minQuarterSelection_0q")
        m.addConstr(gp.quicksum(Anq[d,1] for d in range(nNightsInSemester)) >= minQuarterSelection, "minQuarterSelection_1q")
        m.addConstr(gp.quicksum(Anq[d,2] for d in range(nNightsInSemester)) >= minQuarterSelection, "minQuarterSelection_2q")
        m.addConstr(gp.quicksum(Anq[d,3] for d in range(nNightsInSemester)) >= minQuarterSelection, "minQuarterSelection_3q")

        print("Constraint: forbid certain patterns of quarter night allocations within night.")
        # Disallow certain patterns of quarters selected within same night
        for d in range(nNightsInSemester):
            # Cannot have 1st and 3rd quarter allocated without also allocating 2nd quarter (no gap), regardless of if 4th quarter is allocated or not
            m.addConstr(Anq[d,0] + (Un[d]-Anq[d,1]) + Anq[d,2] <= 2*Un[d], "NoGap2_" + str(d) + "d")
            # Cannot have 2nd and 4th quarter allocated without also allocating 3rd quarter (no gap), regardless of if 1st quarter is allocated or not
            m.addConstr(Anq[d,1] + (Un[d]-Anq[d,2]) + Anq[d,3] <= 2*Un[d], "NoGap3_" + str(d) + "d")
            # Cannot have only 2nd and 3rd quarters allocated (no middle half)
            m.addConstr((Un[d]-Anq[d,0]) + Anq[d,1] + Anq[d,2] + (Un[d]-Anq[d,3]) <= 3*Un[d], "NoMiddleHalf_" + str(d) + "d")
            # Cannot have only 1st and 4th quarters allocated (no end-cap half)
            m.addConstr(Anq[d,0] + (Un[d]-Anq[d,1]) + (Un[d]-Anq[d,2]) + Anq[d,3] <= 3*Un[d], "NoEndCapHalf_" + str(d) + "d")
            # Cannot choose single quarter allocations
            m.addConstr(Anq[d,0] + (Un[d]-Anq[d,1]) + (Un[d]-Anq[d,2]) + (Un[d]-Anq[d,3]) <= 3*Un[d], "No1stQOnly_" + str(d) + "d")
            m.addConstr((Un[d]-Anq[d,0]) + Anq[d,1] + (Un[d]-Anq[d,2]) + (Un[d]-Anq[d,3]) <= 3*Un[d], "No2ndQOnly_" + str(d) + "d")
            m.addConstr((Un[d]-Anq[d,0]) + (Un[d]-Anq[d,1]) + Anq[d,2] + (Un[d]-Anq[d,3]) <= 3*Un[d], "No3rdQOnly_" + str(d) + "d")
            m.addConstr((Un[d]-Anq[d,0]) + (Un[d]-Anq[d,1]) + (Un[d]-Anq[d,2]) + Anq[d,3] <= 3*Un[d], "No4thQOnly_" + str(d) + "d")
            # Cannot choose 3/4 allocations
            m.addConstr(Anq[d,0] + Anq[d,1] + Anq[d,2] + (Un[d]-Anq[d,3]) <= 3*Un[d], "No3/4Q_v1_" + str(d) + "d")
            m.addConstr((Un[d]-Anq[d,0]) + Anq[d,1] + Anq[d,2] + Anq[d,3] <= 3*Un[d], "No3/4Q_v2_" + str(d) + "d")

        # enforce that certain nights/quarters CANNOT or MUST be chosen
        if enforcedNO_file != 'nofilename.csv':
            print("Constraint: enforcing quarters that cannot be chosen.")
            enforcedNO = hf.buildEnforcedDates(enforcedNO_file, all_dates_dict)
            for i in range(len(enforcedNO)):
                night = enforcedNO[i][0]
                quart = enforcedNO[i][1]
                m.addConstr(Anq[night,quart] == 0, "enforcedNO_" + str(night) + "d_" + str(quart) + 'q')
        else:
            print("No specific quarters forbidden from being chosen.")
        if enforcedYES_file != 'nofilename.csv':
            print("Constraint: enforcing quarters that must be chosen.")
            enforcedYES = hf.buildEnforcedDates(enforcedYES_file, all_dates_dict)
            for i in range(len(enforcedYES)):
                night = enforcedYES[i][0]
                quart = enforcedYES[i][1]
                m.addConstr(Anq[night,quart] == 1, "enforcedYES_" + str(night) + "d_" + str(quart) + 'q')
        else:
            print("No specific quarters have to be chosen.")

        # print("Constraint: setting max number of consecutive unique nights allocated.")
        # # Don't allocate more than X consecutive nights
        # consecMax = 6
        # for d in range(nNightsInSemester - consecMax):
        #     m.addConstr(gp.quicksum(Un[d + t] for t in range(consecMax)) <= consecMax - 1, "consecutiveNightsMax_" + str(d) + "d")

        # print("Constraint: setting min number of consecutive unique nights not allocated.")
        # Enforce at least one day allocated every X days (no large gaps)
        # commenting this out for 2024B due to the KPF servicing mission forcing an extended shutdown
        # maxGap = 10
        # for d in range(nNightsInSemester - maxGap):
        #     m.addConstr(gp.quicksum(Un[d + t] for t in range(maxGap)) >= 2, "noLargeGaps_" + str(d) + "d")

        # print("Constraint: maximize the baseline of unique nights allocated.")
        # # Enforce a night to be allocated within the first X nights and the last X nights of the semester (max baseline)
        # maxBase = 5
        # m.addConstr(gp.quicksum(Un[0 + t] for t in range(maxBase)) >= 1, "maxBase_early")
        # m.addConstr(gp.quicksum(Un[nNightsInSemester - maxBase + t] for t in range(maxBase)) >= 1, "maxBase_late")

    else:
        print("Add the semester solver constraints.")

        print("Constraint: enforce twilight times")
        print("Constraint: only observe if accessible")
        print("Constraint: enforce no observations when not allocated.")
        for name in all_targets_frame['Starname']:
            all_access = np.array(accessmaps_precompute[name]).flatten()
            startslot = (all_dates_dict[current_day])*nSlotsInNight # plus 1 to account for python indexing?
            access = all_access[startslot:]
            fullmap = alloAndTwiMap&access[:len(alloAndTwiMap)]
            for s in range(nSlotsInSemester):
                m.addConstr(Yns[name,s] <= fullmap[s], 'enforceMaps_' + str(name) + "_" + str(s) + "s")

        print("Constraint: first observation of new schedule can't violate past inter-night cadence.")
        for t,row in all_targets_frame.iterrows():
            name = row['Starname']
            internightcadence = int(row['Inter_Night_Cadence'])
            if pastObs_Info == {}:
                dateLastObserved = '0000-00-00'
            else:
                dateLastObserved = pastObs_Info[name][0]
            if dateLastObserved != '0000-00-00':
                dateLastObserved_number = all_dates_dict[dateLastObserved]
                today_number = all_dates_dict[current_day]
                diff = today_number - dateLastObserved_number
                if diff < internightcadence:
                    doublediff = internightcadence - diff
                    for e in range(doublediff):
                        m.addConstr(Wnd[name,e] == 0, 'enforce_internightCadence_fromStart_' + str(name) + "_" + str(e) + "e")

    if nonqueueMap != 'nofilename.csv':
        print("Constraint: certain slots on allocated nights must be zero to accommodate Non-Queue observations.")
        nonqueuemap_slots_ints = np.loadtxt(nonqueueMap, delimiter=',', dtype=int)
        nonqueuemap_slots_ints = nonqueuemap_slots_ints.flatten()
        for s in range(nSlotsInSemester):
            nonqueueslot = int(nonqueuemap_slots_ints[s + startingSlot])
            for name in all_targets_frame['Starname']:
                m.addConstr(Yns[name,s] <= nonqueueslot, 'enforce_NonQueueSlots_' + str(name) + "_" + str(s) + "s")
    else:
        print("No non-queue observations are scheduled.")

    endtime2 = time.time()
    print("Total Time to build constraints: ", np.round(endtime2-start_all,3))

    # -------------------------------------------------------------------
    # -------------------------------------------------------------------
    #               Set Gorubi Objective and Run
    # -------------------------------------------------------------------
    # -------------------------------------------------------------------
    print("Setting objective.")

    # Minimize Theta_n
    m.setObjective(gp.quicksum(Thn[name] for name in all_targets_frame['Starname']), GRB.MINIMIZE)

    m.params.TimeLimit = SolveTime
    m.Params.OutputFlag = gurobi_output
    m.params.MIPGap = 0.50 # can stop at 1% gap or better to prevent it from spending lots of time on marginally better solutions
    m.params.Presolve = 2 # more aggressive presolve gives better solution
    m.update()
    m.optimize()

    if m.Status == GRB.INFEASIBLE:
        print('Model remains infeasible. Searching for invalid constraints')
        search = m.computeIIS()
        print("Printing bad constraints:")
        for c in m.getConstrs():
            if c.IISConstr:
                print('%s' % c.ConstrName)
        for c in m.getGenConstrs():
            if c.IISGenConstr:
                print('%s' % c.GenConstrName)
    else:
        print("")
        print("")
        print("Round 1 Model Solved.")

    endtime3 = time.time()
    print("Total Time to finish solver: ", np.round(endtime3-start_all,3))


    # -------------------------------------------------------------------
    # -------------------------------------------------------------------
    #               Retrieve data from solution
    # -------------------------------------------------------------------
    # -------------------------------------------------------------------
    file = open(outputdir + "runReport.txt", "w")
    file.close()

    if runOptimalAllocation == True:
        print("Reading results of optimal allocation map.")
        allocation_schedule_1d = []
        for v in Anq.values():
            if np.round(v.X,0) == 1:
                allocation_schedule_1d.append(1)
            else:
                allocation_schedule_1d.append(0)
        allocation_schedule = np.reshape(allocation_schedule_1d, (nNightsInSemester, nQuartersInNight))
        allocation_schedule_full = allocation_schedule
        holder = np.zeros(np.shape(allocation_schedule))
        #allocation_map, allocation_map_NS, weathered_map = hf.buildNonAllocatedMap(allocation_schedule, holder, AvailableSlotsInGivenNight, nSlotsInNight, nNightsInSemester, nQuartersInNight, nSlotsInQuarter, nSlotsInNight)
        allocation_map, allocation_map_NS, weathered_map = mf.buildAllocationMap(allocation_schedule, holder, AvailableSlotsInGivenNight, twilightMap_all)
        print("Building human readable schedule.")
        combined_semester_schedule = hf.buildHumanReadableSchedule(Yns, twilightMap_all, all_targets_frame, nNightsInSemester, nSlotsInNight, AvailableSlotsInGivenNight, nSlotsInQuarter, all_dates_dict, current_day, allocation_map_NS, weathered_map, slotsNeededDict, nonqueueMap_str)
    else:
        print("Building human readable schedule.")
        combined_semester_schedule = hf.buildHumanReadableSchedule(Yns, twilightMap_all, all_targets_frame, nNightsInSemester, nSlotsInNight, AvailableSlotsInGivenNight, nSlotsInQuarter, all_dates_dict, current_day, allocation_map_NS, weathered_map, slotsNeededDict, nonqueueMap_str)

    round = 'Round 1'
    rf.buildFullnessReport(allocation_map, twilightMap_all, combined_semester_schedule, nSlotsInQuarter, nSlotsInSemester, all_targets_frame, outputdir, STEP, round)
    np.savetxt(outputdir + 'raw_combined_semester_schedule.txt', combined_semester_schedule, delimiter=',', fmt="%s")

    print("Writing Report.")
    filename = open(outputdir + "runReport.txt", "a")
    theta_n_var = []
    counter = 0
    for v in Thn.values():
        varname = v.VarName
        varval = v.X
        counter += varval
    print("Sum of Theta: " + str(counter))
    filename.write("Sum of Theta: " + str(counter) + "\n")

    if plot_results:
        print("Plotting results.")
        if runOptimalAllocation:
            allocation_schedule_full_semester = allocation_schedule_full
        all_starmaps = {}
        # Only generate the COF for results of Round 1.
        for i in range(len(all_targets_frame)):
            if pastObs_Info != {}:
                starmap = rf.buildObservedMap_past(pastObs_Info[all_targets_frame['Starname'][i]][1], pastObs_Info[all_targets_frame['Starname'][i]][2], pastObs_Info[all_targets_frame['Starname'][i]][3], starmap_template_filename)
            else:
                starmap = pd.read_csv(starmap_template_filename)
                starmap_columns = starmap.columns
                if 'Observed' not in starmap_columns:
                    starmap['Observed'] = [False]*len(starmap)
                if 'N_obs' not in starmap_columns:
                    starmap['N_obs'] = [0]*len(starmap)
                unique_hstdates_observed = []
            starmap_updated = rf.buildObservedMap_future(all_targets_frame['Starname'][i], slotsNeededDict[all_targets_frame['Starname'][i]], combined_semester_schedule, starmap, all_dates_dict[current_day], slotsNeededDict, np.array(allocation_schedule_full_semester))
            #if optimalAllocation:
            #    pd.to_csv(outputdir + "/FirstForecasts/Forecast_" + str(all_targets_frame['Starname'][i]) + "_semester.csv", index=False)
            all_starmaps[all_targets_frame['Starname'][i]] = starmap_updated
            rf.writeCadencePlotFile(all_targets_frame['Starname'][i], i, starmap, turnFile, all_targets_frame, outputdir, unique_hstdates_observed, current_day)
        compareFlag = not runOptimalAllocation

        rf.buildAllocationPicture(allocation_schedule_full, nNightsInSemester, nQuartersInNight, startingNight, all_dates_dict, outputdir)
        sum_schedule = np.sum(allocation_schedule_full_semester, axis=1)
        allocated_days = np.nonzero(sum_schedule)
        firstDayAllocated = dates_in_semester[allocated_days[0][0]]
        lastDayAllocated = dates_in_semester[allocated_days[0][-1]]
        notable_dates = [semester_start_date, firstDayAllocated, lastDayAllocated, semester_end_date]
        rf.buildCOF(outputdir, current_day, all_targets_frame, all_dates_dict, all_starmaps, notable_dates, False)#compareFlag)

    endtime4 = time.time()
    print("Total Time to complete Round 1: " + str(np.round(endtime4-start_all,3)))
    filename.write("Total Time to complete Round 1: " + str(np.round(endtime4-start_all,3)) + "\n")
    filename.write("\n")
    filename.write("\n")
    filename.close()
    print("Round 1 complete.")


    # -------------------------------------------------------------------
    # -------------------------------------------------------------------
    #               Initiate Round 2 Scheduling
    # -------------------------------------------------------------------
    # -------------------------------------------------------------------

    if runRound2:
        print("Beginning Round 2 Scheduling.")
        # Extract theta values:
        first_stage_objval = m.objval
        eps = 5 # allow a tolerance

        m.params.TimeLimit = SolveTime
        m.Params.OutputFlag = gurobi_output
        m.params.MIPGap = 0.05 # stop at 5% gap, this is good enough and leaves some room for including standard stars
        m.addConstr(gp.quicksum(Thn[name] for name in all_targets_frame['Starname']) <= first_stage_objval + eps)
        m.setObjective(gp.quicksum(slotsNeededDict[name]*Yns[name,s] for name in all_targets_frame['Starname'] for s in range(nSlotsInSemester)), GRB.MAXIMIZE)
        m.update()
        m.optimize()

        if m.Status == GRB.INFEASIBLE:
            print('Model remains infeasible. Searching for invalid constraints')
            search = m.computeIIS()
            print("Printing bad constraints:")
            for c in m.getConstrs():
                if c.IISConstr:
                    print('%s' % c.ConstrName)
            for c in m.getGenConstrs():
                if c.IISGenConstr:
                    print('%s' % c.GenConstrName)
        else:
            print("")
            print("")
            print("Round 1 Model Solved.")

        endtime5 = time.time()
        print("Total Time to finish round 2 solver: ", np.round(endtime5-start_all,3))

        combined_semester_schedule = hf.buildHumanReadableSchedule(Yns, twilightMap_all, all_targets_frame, nNightsInSemester, nSlotsInNight, AvailableSlotsInGivenNight, nSlotsInQuarter, all_dates_dict, current_day, allocation_map_NS, weathered_map, slotsNeededDict)

        round = 'Round 2'
        rf.buildFullnessReport(allocation_schedule, twilightMap_all, combined_semester_schedule, nSlotsInQuarter, nSlotsInSemester, all_targets_frame, outputdir, STEP, round)
        np.savetxt(outputdir + 'Bonus_raw_combined_semester_schedule.txt', combined_semester_schedule, delimiter=',', fmt="%s")

        filename = open(outputdir + "runReport.txt", "a")
        theta_n_var = []
        counter = 0
        for v in Thn.values():
            varname = v.VarName
            varval = v.X
            counter += varval
        filename.write("Sum of Theta: " + str(counter) + "\n")

        endtime6 = time.time()
        filename.write("Total Time to complete Round 2: " + str(np.round(endtime5-start_all,3)) + "\n")
        filename.write("\n")
        filename.write("\n")
        filename.close()
        print("Round 2 complete.")

    print("done")
