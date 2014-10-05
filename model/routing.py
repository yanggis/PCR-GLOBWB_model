#!/usr/bin/ python
# -*- coding: utf-8 -*-

import os

from pcraster.framework import *
import pcraster as pcr

import logging
logger = logging.getLogger(__name__)

import virtualOS as vos
from ncConverter import *

import waterBodies

class Routing(object):
    
    #TODO: remove
    def getPseudoState(self):
        result = {}
        return result

    #TODO: remove
    def getVariables(self, names):
        result = {}
        return result

    def getState(self):
        result = {}
        
        result['timestepsToAvgDischarge']  = self.timestepsToAvgDischarge     

        result['channelStorage']           = self.channelStorage             #  m3      ; channel storage, including lake and reservoir storage 
        result['readAvlChannelStorage']    = self.readAvlChannelStorage      #  m3      ; readily available channel storage that can be extracted to satisfy water demand
        result['avgDischargeLong']         = self.avgDischarge               #  m3/s    ;  long term average discharge
        result['m2tDischargeLong']         = self.m2tDischarge               # (m3/s)^2
        
        result['avgBaseflowLong']          = self.avgBaseflow                #  m3/s    ;  long term average baseflow
        result['riverbedExchange']         = self.riverbedExchange           #  m3/day  : river bed infiltration (from surface water bdoies to groundwater)
        
        result['avgLakeReservoirOutflowLong'] = self.avgOutflow             #  m3/s    ; long term average lake & reservoir outflow
        result['avgLakeReservoirInflowShort'] = self.avgInflow              #  m3/s    ; short term average lake & reservoir inflow

        # New state variables introduced in the version 2.0.2 
        # - These variables are only relevant for
        #   the option allow_negative_channel_storage = True.
        #  
        result['avgDischargeShort']        = self.avgDischargeShort          #  m3/s    ; short term average discharge 
        result['avgSurfaceWaterInputLong'] = self.avgSurfaceWaterInput       #  m3/s    ; short term average surface water input (runoff, return flow, water body evaporation, river bed exchange, lake & reservoir outflow, etc.)                        

        return result

    def __init__(self,iniItems,initialConditions,lddMap):
        object.__init__(self)

        # if the following is True, channel storage can be negative
        self.allow_negative_channel_storage = False
        try:
            if iniItems.routingOptions['allow_negative_channel_storage'] == "True":\
                   self.allow_negative_channel_storage = True
        except:
            self.allow_negative_channel_storage = False        

        self.lddMap = lddMap

        self.cloneMap = iniItems.cloneMap
        self.tmpDir = iniItems.tmpDir
        self.inputDir = iniItems.globalOptions['inputDir']

        self.debugWaterBalance = iniItems.routingOptions['debugWaterBalance']

        #~ self.method = iniItems.routingOptions['routingMethod']
        self.method = "accuTravelTime"                                  # Currently, we only support "accuTravelTime".

        # TODO: 26 Feb 2014, Edwin found that reasonable runs are only found 
        # if all of these options = True.                    
        self.includeWaterBodies = True
        self.includeLakes = True
        self.includeReservoirs =  True

        # local drainage direction:
        self.lddMap = vos.readPCRmapClone(\
                  iniItems.routingOptions['lddMap'],
                  self.cloneMap,self.tmpDir,self.inputDir,True)
        self.lddMap = pcr.lddrepair(pcr.ldd(self.lddMap))
        self.lddMap = pcr.lddrepair(self.lddMap)

        # landmask:
        if iniItems.globalOptions['landmask'] != "None":
           self.landmask = vos.readPCRmapClone(\
           iniItems.globalOptions['landmask'],
           self.cloneMap,self.tmpDir,self.inputDir)
        else:   	
           self.landmask = pcr.defined(self.lddMap)
        self.landmask = pcr.ifthen(pcr.defined(self.lddMap), self.landmask)
        self.landmask = pcr.cover(self.landmask, pcr.boolean(0))   

        # ldd mask 
        self.lddMap = pcr.lddmask(self.lddMap, self.landmask)

        self.cellArea = vos.readPCRmapClone(\
                  iniItems.routingOptions['cellAreaMap'],
                  self.cloneMap,self.tmpDir,self.inputDir)

        self.cellSizeInArcDeg = vos.getMapAttributes(self.cloneMap,"cellsize")  

        # maximum number of days (timesteps) to calculate long term average flow values (default: 5 years = 5 * 365 days = 1825)
        self.maxTimestepsToAvgDischargeLong  = 1825.

        # maximum number of days (timesteps) to calculate short term average values (default: 1 month = 1 * 30 days = 30)
        self.maxTimestepsToAvgDischargeShort = 30.                            

        if self.method == "accuTravelTime":

            routingParameters = ['gradient','manningsN']
            for var in routingParameters:
                input = iniItems.routingOptions[str(var)]
                vars(self)[var] = vos.readPCRmapClone(input,\
                                self.cloneMap,self.tmpDir,self.inputDir)

            self.eta = 0.25
            self.nu  = 0.40
            self.tau = 8.00
            self.phi = 0.58

            #  cellLength (m) is approximated cell diagonal   
            cellSizeInArcMin    =  self.cellSizeInArcDeg*60.
            verticalSizeInMeter =  cellSizeInArcMin*1852.                            
            #
            self.cellLengthFD = ((self.cellArea/verticalSizeInMeter)**(2)+\
                                               (verticalSizeInMeter)**(2))\
                                                                    **(0.5) 
            nrCellsDownstream  = pcr.ldddist(self.lddMap,\
                                             self.lddMap == 5,1.)
            distanceDownstream = pcr.ldddist(self.lddMap,\
                                             self.lddMap == 5,\
                                             self.cellLengthFD)
            channelLengthDownstream = \
                    (self.cellLengthFD + distanceDownstream)/\
                    (nrCellsDownstream + 1)                 # unit: m
            self.dist2celllength  = channelLengthDownstream /\
                                      self.cellSizeInArcDeg # unit: m/arcDegree  

        # the channel gradient must be >= minGradient 
        minGradient   = 0.000005
        self.gradient = pcr.max(minGradient,\
                        pcr.cover(self.gradient, minGradient))

        # initiate/create WaterBody class
        self.WaterBodies = waterBodies.WaterBodies(iniItems)

        self.fileCropKC = vos.getFullPath(\
                     iniItems.routingOptions['cropCoefficientWaterNC'],\
                     self.inputDir)

        # get the initialconditions
        self.getICs(iniItems, initialConditions)

        self.report = True
        try:
            self.outDailyTotNC = iniItems.routingOptions['outDailyTotNC'].split(",")
            self.outMonthTotNC = iniItems.routingOptions['outMonthTotNC'].split(",")
            self.outMonthAvgNC = iniItems.routingOptions['outMonthAvgNC'].split(",")
            self.outMonthEndNC = iniItems.routingOptions['outMonthEndNC'].split(",")
            self.outAnnuaTotNC = iniItems.routingOptions['outAnnuaTotNC'].split(",")
            self.outAnnuaAvgNC = iniItems.routingOptions['outAnnuaAvgNC'].split(",")
            self.outAnnuaEndNC = iniItems.routingOptions['outAnnuaEndNC'].split(",")
        except:
            self.report = False
        if self.report == True:
            # daily output in netCDF files:
            self.outNCDir  = iniItems.outNCDir
            self.netcdfObj = PCR2netCDF(iniItems)
            #
            if self.outDailyTotNC[0] != "None":
                for var in self.outDailyTotNC:
                    # creating the netCDF files:
                    self.netcdfObj.createNetCDF(str(self.outNCDir)+"/"+ \
                                                str(var)+"_dailyTot.nc",\
                                                    var,"undefined")
            # MONTHly output in netCDF files:
            # - cummulative
            if self.outMonthTotNC[0] != "None":
                for var in self.outMonthTotNC:
                    # initiating monthlyVarTot (accumulator variable):
                    vars(self)[var+'MonthTot'] = None
                    # creating the netCDF files:
                    self.netcdfObj.createNetCDF(str(self.outNCDir)+"/"+ \
                                                str(var)+"_monthTot.nc",\
                                                    var,"undefined")
            # - average
            if self.outMonthAvgNC[0] != "None":
                for var in self.outMonthAvgNC:
                    # initiating monthlyTotAvg (accumulator variable)
                    vars(self)[var+'MonthTot'] = None
                    # initiating monthlyVarAvg:
                    vars(self)[var+'MonthAvg'] = None
                     # creating the netCDF files:
                    self.netcdfObj.createNetCDF(str(self.outNCDir)+"/"+ \
                                                str(var)+"_monthAvg.nc",\
                                                    var,"undefined")
            # - last day of the month
            if self.outMonthEndNC[0] != "None":
                for var in self.outMonthEndNC:
                     # creating the netCDF files:
                    self.netcdfObj.createNetCDF(str(self.outNCDir)+"/"+ \
                                                str(var)+"_monthEnd.nc",\
                                                    var,"undefined")
            # YEARly output in netCDF files:
            # - cummulative
            if self.outAnnuaTotNC[0] != "None":
                for var in self.outAnnuaTotNC:
                    # initiating yearly accumulator variable:
                    vars(self)[var+'AnnuaTot'] = None
                    # creating the netCDF files:
                    self.netcdfObj.createNetCDF(str(self.outNCDir)+"/"+ \
                                                str(var)+"_annuaTot.nc",\
                                                    var,"undefined")
            # - average
            if self.outAnnuaAvgNC[0] != "None":
                for var in self.outAnnuaAvgNC:
                    # initiating annualyVarAvg:
                    vars(self)[var+'AnnuaAvg'] = None
                    # initiating annualyTotAvg (accumulator variable)
                    vars(self)[var+'AnnuaTot'] = None
                     # creating the netCDF files:
                    self.netcdfObj.createNetCDF(str(self.outNCDir)+"/"+ \
                                                str(var)+"_annuaAvg.nc",\
                                                    var,"undefined")
            # - last day of the year
            if self.outAnnuaEndNC[0] != "None":
                for var in self.outAnnuaEndNC:
                     # creating the netCDF files:
                    self.netcdfObj.createNetCDF(str(self.outNCDir)+"/"+ \
                                                str(var)+"_annuaEnd.nc",\
                                                    var,"undefined")

    def getICs(self,iniItems,iniConditions = None):

        if iniConditions == None:

            # read initial conditions from pcraster maps listed in the ini file (for the first time step of the model; when the model just starts)
            
            self.timestepsToAvgDischarge = vos.readPCRmapClone(iniItems.routingOptions['timestepsToAvgDischargeIni'] ,self.cloneMap,self.tmpDir,self.inputDir)  
            
            self.channelStorage          = vos.readPCRmapClone(iniItems.routingOptions['channelStorageIni']          ,self.cloneMap,self.tmpDir,self.inputDir)
            self.readAvlChannelStorage   = vos.readPCRmapClone(iniItems.routingOptions['readAvlChannelStorageIni']   ,self.cloneMap,self.tmpDir,self.inputDir) 
            self.avgDischarge            = vos.readPCRmapClone(iniItems.routingOptions['avgDischargeLongIni']        ,self.cloneMap,self.tmpDir,self.inputDir) 
            self.m2tDischarge            = vos.readPCRmapClone(iniItems.routingOptions['m2tDischargeLongIni']        ,self.cloneMap,self.tmpDir,self.inputDir) 
            self.avgBaseflow             = vos.readPCRmapClone(iniItems.routingOptions['avgBaseflowLongIni']         ,self.cloneMap,self.tmpDir,self.inputDir) 
            self.riverbedExchange        = vos.readPCRmapClone(iniItems.routingOptions['riverbedExchangeIni']        ,self.cloneMap,self.tmpDir,self.inputDir) 
            
            # New initial condition variables introduced in the version 2.0.2: avgDischargeShort and avgSurfaceWaterInput 
            # - These variables are only relevant for
            #   the option allow_negative_channel_storage = True.
            self.avgDischargeShort       = vos.readPCRmapClone(iniItems.routingOptions['avgDischargeShortIni']       ,self.cloneMap,self.tmpDir,self.inputDir) 
            self.avgSurfaceWaterInput    = vos.readPCRmapClone(iniItems.routingOptions['avgSurfaceWaterInputLongIni'],self.cloneMap,self.tmpDir,self.inputDir) 

        else:              

            # read initial conditions from the memory

            self.timestepsToAvgDischarge = iniConditions['routing']['timestepsToAvgDischarge']              
                                                                   
            self.channelStorage          = iniConditions['routing']['channelStorage']
            self.readAvlChannelStorage   = iniConditions['routing']['readAvlChannelStorage']
            self.avgDischarge            = iniConditions['routing']['avgDischargeLong']
            self.m2tDischarge            = iniConditions['routing']['m2tDischargeLong']
            self.avgBaseflow             = iniConditions['routing']['avgBaseflowLong']
            self.riverbedExchange        = iniConditions['routing']['riverbedExchange']
            self.avgDischargeShort       = iniConditions['routing']['avgDischargeShort']
            self.avgSurfaceWaterInput    = iniConditions['routing']['avgSurfaceWaterInputLong']
            
        self.channelStorage        = pcr.ifthen(self.landmask, pcr.cover(self.channelStorage,        0.0))
        self.readAvlChannelStorage = pcr.ifthen(self.landmask, pcr.cover(self.readAvlChannelStorage, 0.0))
        self.avgDischarge          = pcr.ifthen(self.landmask, pcr.cover(self.avgDischarge,          0.0))
        self.m2tDischarge          = pcr.ifthen(self.landmask, pcr.cover(self.m2tDischarge,          0.0))
        self.avgDischargeShort     = pcr.ifthen(self.landmask, pcr.cover(self.avgDischargeShort,     0.0))
        self.avgBaseflow           = pcr.ifthen(self.landmask, pcr.cover(self.avgBaseflow,           0.0))
        self.avgSurfaceWaterInput  = pcr.ifthen(self.landmask, pcr.cover(self.avgSurfaceWaterInput,  0.0))
        self.riverbedExchange      = pcr.ifthen(self.landmask, pcr.cover(self.riverbedExchange,      0.0))

        self.readAvlChannelStorage = pcr.min(self.readAvlChannelStorage, self.channelStorage)
        self.readAvlChannelStorage = pcr.max(self.readAvlChannelStorage, 0.0)
        
        # make sure that timestepsToAvgDischarge is consistent (or the same) for the entire map:
        try:
            self.timestepsToAvgDischarge = pcr.mapmaximum(self.timestepsToAvgDischarge)
        except:    
            pass # We have to use 'try/except' because 'pcr.mapmaximum' cannot handle scalar value

        self.timestepsToAvgDischarge = pcr.ifthen(self.landmask, self.timestepsToAvgDischarge)
        
        # Initial conditions needed for water bodies:
        # - initial short term average inflow (m3/s) and 
        #           long term average outflow (m3/s)
        if iniConditions == None:
            # read initial conditions from pcraster maps listed in the ini file (for the first time step of the model; when the model just starts)
            self.avgInflow  = vos.readPCRmapClone(iniItems.routingOptions['avgLakeReservoirInflowShortIni'],self.cloneMap,self.tmpDir,self.inputDir)
            self.avgOutflow = vos.readPCRmapClone(iniItems.routingOptions['avgLakeReservoirOutflowLongIni'],self.cloneMap,self.tmpDir,self.inputDir)
        else:
            # read initial conditions from the memory
            self.avgInflow  = iniConditions['routing']['avgLakeReservoirInflowShort']
            self.avgOutflow = iniConditions['routing']['avgLakeReservoirOutflowLong']


    def getRoutingParamAvgDischarge(self, avgDischarge, dist2celllength):
        # obtain routing parameters based on average (longterm) discharge
        # output: channel dimensions and 
        #         characteristicDistance (for accuTravelTime input)
        
        yMean = self.eta * pow (avgDischarge, self.nu ) # avgDischarge in m3/s
        wMean = self.tau * pow (avgDischarge, self.phi)
 
        yMean =   pcr.max(yMean,0.000000001) # channel depth (m)
        wMean =   pcr.max(wMean,0.000000001) # channel width (m)
        yMean = pcr.cover(yMean,0.000000001)
        wMean = pcr.cover(wMean,0.000000001)
        
        # characteristicDistance (dimensionless)
        # - This will be used for accutraveltimeflux & accutraveltimestate
        # - discharge & storage = accutraveltimeflux & accutraveltimestate
        # - discharge = the total amount of material flowing through the cell (m3/s)
        # - storage   = the amount of material which is deposited in the cell (m3)
        #
        characteristicDistance = \
             ( (yMean *   wMean)/ \
               (wMean + 2*yMean) )**(2./3.) * \
              ((self.gradient)**(0.5))/ \
                self.manningsN * \
                vos.secondsPerDay()                         #  meter/day

        characteristicDistance = \
         pcr.max((self.cellSizeInArcDeg)*0.000000001,\
                 characteristicDistance/dist2celllength)    # arcDeg/day
        
        # charateristicDistance for each lake/reservoir:
        lakeReservoirCharacteristicDistance = pcr.ifthen(pcr.scalar(self.WaterBodies.waterBodyIds) > 0.,
                                              pcr.areaaverage(characteristicDistance, self.WaterBodies.waterBodyIds))
        #
        # - make sure that all outflow will be released outside lakes and reservoirs
        outlets = pcr.cover(pcr.ifthen(pcr.scalar(self.WaterBodies.waterBodyOut) > 0, pcr.boolean(1)), pcr.boolean(0))
        distance_to_outlets = pcr.ifthen(pcr.scalar(self.WaterBodies.waterBodyIds) > 0.,
                              pcr.ldddist(self.lddMap, outlets, pcr.scalar(1.0)))
        lakeReservoirCharacteristicDistance = pcr.ifthen(pcr.scalar(self.WaterBodies.waterBodyIds) > 0.,
                                              pcr.max(distance_to_outlets + pcr.downstreamdist(self.lddMap)*1.50, lakeReservoirCharacteristicDistance))
        #
        # TODO: calculate lakeReservoirCharacteristicDistance while obtaining lake & reservoir parameters
        
        characteristicDistance = pcr.cover(lakeReservoirCharacteristicDistance, characteristicDistance)                      
        
        # PS: In accutraveltime function: 
        #     If characteristicDistance (velocity) = 0 then:
        #     - accutraveltimestate will give zero 
        #     - accutraveltimeflux will be very high 
        
        # TODO: Consider to use downstreamdist function.
        
        # current solution: using the function "roundup" to ignore 
        #                   zero and very small values 
        characteristicDistance = \
         pcr.roundup(characteristicDistance*10000.)/10000.    # arcDeg/day
        
        # and set minimum value of characteristicDistance:
        characteristicDistance = pcr.cover(characteristicDistance, 0.1*self.cellSizeInArcDeg)
        characteristicDistance = pcr.max(0.100*self.cellSizeInArcDeg, characteristicDistance)         # TODO: check what the minimum distance for accutraveltime function

        return (yMean, wMean, characteristicDistance)

    def accuTravelTime(self,currTimeStep):
        		
        # route only non negative channelStorage (otherwise stay):
        channelStorageThatWillNotMove = pcr.ifthenelse(self.channelStorage < 0.0, self.channelStorage, 0.0)
        self.channelStorage           = pcr.max(0.0, self.channelStorage)
        
        # also at least 1.0 m3 of water will stay - this is needed to 
        channelStorageThatWillNotMove += self.channelStorage - pcr.rounddown(self.channelStorage)
        self.channelStorage            = pcr.rounddown(self.channelStorage) 
        
        # channelStorage that will be given to the ROUTING operation:
        channelStorageForAccuTravelTime = pcr.max(0.0, self.channelStorage)
        channelStorageForAccuTravelTime = pcr.cover(channelStorageForAccuTravelTime,0.0)              # TODO: check why do we have to use the "cover" operation. 
        
        # self.Q = channel discharge (m3/day)
        self.Q = pcr.accutraveltimeflux(self.lddMap,\
                                        channelStorageForAccuTravelTime,\
                                        self.characteristicDistance)
        self.Q = pcr.cover(self.Q, 0.0)
        # for very small velocity (i.e. characteristicDistanceForAccuTravelTime), discharge can be missing value.
        # see: http://sourceforge.net/p/pcraster/bugs-and-feature-requests/543/
        #      http://karssenberg.geo.uu.nl/tt/TravelTimeSpecification.htm        

        # updating channelStorage (after routing)
        self.channelStorage = pcr.accutraveltimestate(self.lddMap,\
                              channelStorageForAccuTravelTime,\
                              self.characteristicDistance)

        # return channelStorageThatWillNotMove to channelStorage:
        self.channelStorage += channelStorageThatWillNotMove 
        
        # reduce discharge in cells with negative channelStorage (assume there are diversion structures)
        if self.allow_negative_channel_storage == True: # NOTE: THIS IS STILL EXPERIMENTAL !!!
            negative_storage = pcr.ifthenelse(self.channelStorage < 0.0, self.channelStorage, 0.0) * \
                               pcr.scalar(-1.0)                                                      # unit: m3
            negative_storage = pcr.cover(negative_storage, 0.0)                                      # TODO: check why do we have to use the "cover" operation. 
            negative_storage_not_moving = negative_storage - pcr.rounddown(negative_storage) 
            negative_storage = pcr.rounddown(negative_storage)
            negative_Q = pcr.accutraveltimeflux(self.lddMap,\
                                                negative_storage,\
                                                self.characteristicDistance)                         # unit: m3/day
            self.Q -= pcr.cover(negative_Q, 0.0)                                                     # unit: m3/day
            self.Q -= pcr.cover(negative_storage_not_moving, 0.0)
        
        self.Q = pcr.ifthen(self.landmask, self.Q)
        # report to the log file if there are some cells with negative discharge (Q)
        a,b,c = vos.getMinMaxMean(pcr.ifthen(self.Q < 0.0, self.Q/vos.secondsPerDay()), True)        # unit: m3/s
        if abs(a) > 1e-3:
            report_negative_storage  = "\n"
            report_negative_storage += "\n"
            report_negative_storage += "\n"
            report_negative_storage += "WARNING! There are some cells with negative discharge (Q) ... Min %f Max %f Mean %f (unit: m3/s) " %(a,b,c)
            report_negative_storage += "\n"
            report_negative_storage += "\n"
            report_negative_storage += "\n"
            logger.info(report_negative_storage)

        # after routing, return waterBodyStorage to channelStorage  
        waterBodyStorageTotal = \
         pcr.ifthen(pcr.scalar(self.WaterBodies.waterBodyIds) > 0.,
         pcr.areaaverage(\
         pcr.ifthen(self.landmask,self.WaterBodies.waterBodyStorage),\
         pcr.ifthen(self.landmask,self.WaterBodies.waterBodyIds)) + \
         pcr.areatotal(pcr.cover(\
         pcr.ifthen(self.landmask,self.channelStorage), 0.0),\
         pcr.ifthen(self.landmask,self.WaterBodies.waterBodyIds)))
        waterBodyStoragePerCell = \
         waterBodyStorageTotal*\
                       self.cellArea/\
         pcr.areatotal(pcr.cover(\
         self.cellArea, 0.0),\
         pcr.ifthen(self.landmask,self.WaterBodies.waterBodyIds))
        waterBodyStoragePerCell = \
         pcr.ifthen(pcr.scalar(self.WaterBodies.waterBodyIds) > 0.,
         waterBodyStoragePerCell)                         # unit: m3
        #
        self.channelStorage = pcr.cover(waterBodyStoragePerCell, self.channelStorage)  # unit: m3
        self.channelStorage = pcr.ifthen(self.landmask, self.channelStorage)

        # channel discharge (m3/s): current:
        self.discharge = self.Q / vos.secondsPerDay()
        self.discharge = pcr.max(0., self.discharge)                   # reported channel discharge cannot be negative
        self.discharge = pcr.ifthen(self.landmask, self.discharge)
        
        # TODO: simulate over bank flow (estimated from bankfull discharge/wdith) - part of the channel storage will be given to landSurface part

        self.disChanWaterBody = pcr.ifthen(pcr.scalar(self.WaterBodies.waterBodyIds) > 0.,\
                                pcr.areamaximum(self.discharge,self.WaterBodies.waterBodyIds))
        self.disChanWaterBody = pcr.cover(self.disChanWaterBody, self.discharge)
        self.disChanWaterBody = pcr.ifthen(self.landmask, self.disChanWaterBody)
        #
        self.disChanWaterBody = pcr.max(0.,self.disChanWaterBody)      # reported channel discharge cannot be negative


    def update(self,landSurface,groundwater,currTimeStep,meteo):

        logger.info("routing in progress")
        
        # updating timesteps to calculate long and short term statistics values: avgDischarge, avgInflow, avgOutflow, etc.
        self.timestepsToAvgDischarge += 1.

        if self.debugWaterBalance == str('True'):\
           preStorage = self.channelStorage                                                        # unit: m3

        # waterBodies: 
        # - get parameters at the beginning of each year or simulation
        #   also get initial condition (at the beginning of simulation)
        if (currTimeStep.doy == 1) or (currTimeStep.timeStepPCR == 1):
            self.WaterBodies.getParameterFiles(currTimeStep,\
                                               self.cellArea,\
                                               self.lddMap,\
                                               self.cellLengthFD,\
                                               self.cellSizeInArcDeg,\
                                               self.channelStorage,self.avgInflow,self.avgOutflow) # the last line is for the initial conditions of lakes/reservoirs

        # the following variable define total local change (input) to surface water storage bodies # unit: m3 
        # - only local processes; therefore not considering any routing processes
        self.local_input_to_surface_water = pcr.scalar(0.0)          # initiate the variable, start from zero

        # runoff from landSurface cells (unit: m/day)
        self.runoff = landSurface.landSurfaceRunoff +\
                      groundwater.baseflow   
        
        # update channelStorage (unit: m3) after runoff
        self.channelStorage += self.runoff * self.cellArea
        self.local_input_to_surface_water += self.runoff * self.cellArea

        # update channelStorage (unit: m3) after actSurfaceWaterAbstraction 
        self.channelStorage -= landSurface.actSurfaceWaterAbstract * self.cellArea
        self.local_input_to_surface_water -= landSurface.actSurfaceWaterAbstract * self.cellArea

        # reporting channelStorage after surface water abstraction (unit: m3)
        self.channelStorageAfterAbstraction = pcr.ifthen(self.landmask, self.channelStorage) 

        # return flow from (m) non irrigation water demand
        self.nonIrrReturnFlow = landSurface.nonIrrReturnFlowFraction*\
                                landSurface.nonIrrGrossDemand          # m
        nonIrrReturnFlowVol   = self.nonIrrReturnFlow*self.cellArea
        self.channelStorage  += nonIrrReturnFlowVol
        self.local_input_to_surface_water += nonIrrReturnFlowVol

        # get routing parameters (based on avgDischarge)
        self.yMean, self.wMean, self.characteristicDistance = \
                self.getRoutingParamAvgDischarge(self.avgDischarge,\
                self.dist2celllength)

        # simulating water bodies fraction
        channelFraction = pcr.max(0.0, pcr.min(1.0,\
                          self.wMean * self.cellLengthFD / (self.cellArea)))
        self.dynamicFracWat = \
                          pcr.max(channelFraction, self.WaterBodies.fracWat)
        self.dynamicFracWat = pcr.ifthen(self.landmask, self.dynamicFracWat)                  

        # (additional) evaporation from water bodies
        # current principle: 
        # - if landSurface.actualET < waterKC * meteo.referencePotET * self.fracWat
        #   then, we add more evaporation
        #
        if (currTimeStep.day == 1) or (currTimeStep.timeStepPCR == 1):
            waterKC = vos.netcdf2PCRobjClone(self.fileCropKC,'kc', \
                               currTimeStep.fulldate, useDoy = 'month',\
                                       cloneMapFileName = self.cloneMap)
            self.waterKC = pcr.min(1.0,pcr.max(0.0,pcr.cover(waterKC, 0.0)))                       
        #
        # potential evaporation from water bodies (m) - reduced by evaporation that has been calculated in the landSurface module
        self.waterBodyPotEvap = pcr.ifthen(self.landmask, \
                                           pcr.max(0.0,\
                                           self.waterKC * meteo.referencePotET -\
                                           landSurface.actualET ))
        #
        # evaporation volume from water bodies (m3) - limited to available channelStorage
        volLocEvapWaterBody = pcr.min(\
                              pcr.max(0.0,self.channelStorage),
                              self.waterBodyPotEvap * self.dynamicFracWat * self.cellArea)

        # update channelStorage (m3) after evaporation from water bodies
        self.channelStorage = self.channelStorage -\
                              volLocEvapWaterBody
        self.local_input_to_surface_water -= volLocEvapWaterBody
        
        # evaporation (m) from water bodies                             
        self.waterBodyEvaporation = volLocEvapWaterBody / self.cellArea
        self.waterBodyEvaporation = pcr.ifthen(self.landmask, self.waterBodyEvaporation)

        # remaining potential evaporation (m) from water bodies
        self.remainWaterBodyPotEvap = pcr.max(0.0, self.waterBodyPotEvap - self.waterBodyEvaporation)
        
        # riverbed infiltration (m3/day):
        # - current implementation based on Inge's principle (later, will be based on groundater head (MODFLOW) and can be negative)
        # - happening only if 0.0 < baseflow < total_groundwater_abstraction
        # - total_groundwater_allocation = groundwater.allocNonFossilGroundwater + groundwater.unmetDemand
        # - infiltration rate will be based on aquifer saturated conductivity
        # - limited to fracWat
        # - limited to available channelStorage
        # - this infiltration will be handed to groundwater in the next time step
        # - References: de Graaf et al. (2014); Wada et al. (2012); Wada et al. (2010)
        # - TODO: This concept should be IMPROVED. 
        riverbedConductivity  = groundwater.kSatAquifer # unit: m/day
        total_groundwater_abstraction = pcr.max(0.0, groundwater.nonFossilGroundwaterAbs + groundwater.unmetDemand)   # unit: m
        self.riverbedExchange = pcr.max(0.0,\
                                pcr.min(pcr.max(0.0,self.channelStorage),\
                                pcr.ifthenelse(groundwater.baseflow > 0.0, \
                                pcr.ifthenelse(total_groundwater_abstraction > groundwater.baseflow, \
                                riverbedConductivity * self.dynamicFracWat * self.cellArea, \
                                0.0), 0.0)))
        self.riverbedExchange = pcr.cover(self.riverbedExchange, 0.0)                         
        factor = 0.05 # to avoid flip flop
        self.riverbedExchange = pcr.min(self.riverbedExchange, (1.0-factor)*pcr.max(0.0,self.channelStorage))                                                             
        self.riverbedExchange = pcr.ifthenelse(self.channelStorage < 0.0, 0.0, self.riverbedExchange)
        self.riverbedExchange = pcr.cover(self.riverbedExchange, 0.0)
        self.riverbedExchange = pcr.ifthen(self.landmask, self.riverbedExchange)

        # update channelStorage (m3) after riverbedExchange (m3)
        self.channelStorage  -= self.riverbedExchange
        self.local_input_to_surface_water -= self.riverbedExchange

        if self.debugWaterBalance == 'True':\
           vos.waterBalanceCheck([self.runoff,\
                                  self.nonIrrReturnFlow],\
                                 [landSurface.actSurfaceWaterAbstract,self.riverbedExchange/self.cellArea,self.waterBodyEvaporation],\
                                 [           preStorage/self.cellArea],\
                                 [  self.channelStorage/self.cellArea],\
                                   'channelStorage before routing and before lake/reservoir outflow',\
                                  True,\
                                  currTimeStep.fulldate,threshold=1e-4)
        
        ##########################################################################################################################
        # 
        # LAKE AND RESERVOIR OPERATIONS

        if self.debugWaterBalance == str('True'):\
           preStorage = self.channelStorage                                                        # unit: m3

        # at cells where lakes and/or reservoirs defined, move channelStorage to waterBodyStorage
        #
        storageAtLakeAndReservoirs = \
         pcr.ifthen(pcr.scalar(self.WaterBodies.waterBodyIds) > 0.,
                               self.channelStorage)
        storageAtLakeAndReservoirs = pcr.cover(storageAtLakeAndReservoirs,0.0)
        #
        # - move only non negative values and use rounddown values
        storageAtLakeAndReservoirs = pcr.max(0.00, pcr.rounddown(storageAtLakeAndReservoirs))
        self.channelStorage -= storageAtLakeAndReservoirs                    # unit: m3

        # update waterBodyStorage (inflow, storage and outflow)
        self.WaterBodies.update(storageAtLakeAndReservoirs,\
                                self.timestepsToAvgDischarge,\
                                self.maxTimestepsToAvgDischargeShort,\
                                self.maxTimestepsToAvgDischargeLong,\
                                currTimeStep,\
                                self.avgDischarge,\
                                None)

        # transfer outflow from lakes and/or reservoirs to channelStorages
        waterBodyOutflow = pcr.cover(\
                           pcr.ifthen(\
                           self.WaterBodies.waterBodyOut,
                           self.WaterBodies.waterBodyOutflow), 0.0)          # unit: m3/day
        #
        # distribute outflow to water body storage
        waterBodyOutflow = pcr.areaaverage(waterBodyOutflow, self.WaterBodies.waterBodyIds)
        waterBodyOutflow = pcr.ifthen(\
                           pcr.scalar(self.WaterBodies.waterBodyIds) > 0.0,
                           waterBodyOutflow)                                 # unit: m3/day
        waterBodyOutflow = pcr.cover(waterBodyOutflow, 0.0)

        # update channelStorage (m3) after waterBodyOutflow (m3)
        self.channelStorage += waterBodyOutflow
        # Note that local_input_to_surface_water does not include waterBodyOutflow
        
        # obtain new water body storages (for reporting)
        self.waterBodyStorage = pcr.ifthen(self.landmask,\
                                pcr.ifthen(\
         pcr.scalar(self.WaterBodies.waterBodyIds) > 0.,\
                    self.WaterBodies.waterBodyStorage))     # m3

        if self.debugWaterBalance == 'True':\
           vos.waterBalanceCheck([waterBodyOutflow/self.cellArea],\
                                 [storageAtLakeAndReservoirs/self.cellArea],\
                                 [           preStorage/self.cellArea],\
                                 [  self.channelStorage/self.cellArea],\
                                   'channelStorage before routing',\
                                  True,\
                                  currTimeStep.fulldate,threshold=5e-3)

        # short term average inflow (m3/s) and long term average outflow (m3/s) from lake and reservoirs
        self.avgInflow  = pcr.ifthen(self.landmask, pcr.cover(self.WaterBodies.avgInflow , 0.0)) 
        self.avgOutflow = pcr.ifthen(self.landmask, pcr.cover(self.WaterBodies.avgOutflow, 0.0))
        
        ##########################################################################################################################


        ##########################################################################################################################
        # ROUTING OPERATION:
        #
        if self.method == "accuTravelTime": self.accuTravelTime(currTimeStep) 		
        #
        ##########################################################################################################################


        # Calculating avgDischarge
        #
        # - long term average disharge
        #
        dishargeUsed      = pcr.max(0.0, self.discharge)
        dishargeUsed      = pcr.max(dishargeUsed, self.disChanWaterBody)
        #
        deltaAnoDischarge = dishargeUsed - self.avgDischarge  
        self.avgDischarge = self.avgDischarge +\
                            deltaAnoDischarge/\
                            pcr.min(self.maxTimestepsToAvgDischargeLong, self.timestepsToAvgDischarge)
        self.avgDischarge = pcr.max(0.0, self.avgDischarge)                                    
        self.m2tDischarge = self.m2tDischarge + pcr.abs(deltaAnoDischarge*(self.discharge - self.avgDischarge))                             
        self.varDischarge = self.m2tDischarge / \
                            pcr.max(1.,\
                            pcr.min(self.maxTimestepsToAvgDischargeLong, self.timestepsToAvgDischarge)-1.)                             
                          # see: online algorithm on http://en.wikipedia.org/wiki/Algorithms_for_calculating_variance
        self.stdDischarge = pcr.max(self.varDischarge**0.5, 0.0)
        #
        # - short term average disharge
        #
        dishargeUsed           = pcr.max(0.0, self.discharge)
        deltaAnoDischargeShort = dishargeUsed - self.avgDischargeShort  
        self.avgDischargeShort = self.avgDischargeShort +\
                                 deltaAnoDischargeShort/\
                                 pcr.min(self.maxTimestepsToAvgDischargeShort, self.timestepsToAvgDischarge)
        self.avgDischargeShort = pcr.max(0.0, self.avgDischargeShort)                         

        # average baseflow (m3/s)
        # - avgDischarge and avgBaseflow used as proxies for partitioning groundwater and surface water abstractions
        #
        baseflowM3PerSec = groundwater.baseflow * self.cellArea / vos.secondsPerDay()
        deltaAnoBaseflow = baseflowM3PerSec - self.avgBaseflow  
        self.avgBaseflow = self.avgBaseflow +\
                           deltaAnoBaseflow/\
                           pcr.min(self.maxTimestepsToAvgDischargeLong, self.timestepsToAvgDischarge)                
        self.avgBaseflow = pcr.max(0.0, self.avgBaseflow)

        # calculate short term average of "input_to_surface_water" # unit: m3/s
        input_to_surface_water = (self.local_input_to_surface_water + waterBodyOutflow) / vos.secondsPerDay()
        deltaAnoSurfaceWaterInput = input_to_surface_water - self.avgSurfaceWaterInput  
        self.avgSurfaceWaterInput = self.avgSurfaceWaterInput +\
                                    deltaAnoSurfaceWaterInput /\
                                    pcr.min(self.maxTimestepsToAvgDischargeShort, self.timestepsToAvgDischarge)

        # calculate minimum discharge for environmental flow
        minDischargeForEnvironmentalFlow = pcr.max(0., self.avgDischarge - 3.0*self.stdDischarge)
        factor = 0.01 # to avoid flip flop
        minDischargeForEnvironmentalFlow = pcr.max(factor*self.avgDischarge, minDischargeForEnvironmentalFlow)

        # available channelStorage that can be extracted for surface water abstraction
        self.readAvlChannelStorage = pcr.max(0.0,self.channelStorage)                                                             

        if self.allow_negative_channel_storage == True: # NOTE: THIS IS STILL EXPERIMENTAL !!!

            # additional storage - based on the estimate of water 
            #                      that will arrive in the next time step/day 
            additional_storage = pcr.cover(\
                                 pcr.accutraveltimestate(self.lddMap,\
                                 pcr.max(0.0, self.avgSurfaceWaterInput * vos.secondsPerDay()),\
                                 self.characteristicDistance), 0.0)
            #
            # additional storage - discharge at pits
            discharge_at_pits = pcr.ifthen(self.lddMap == 5, pcr.max(0.0, \
                                                             pcr.min(self.discharge, self.avgDischarge, self.avgDischargeShort) - minDischargeForEnvironmentalFlow))
            discharge_at_pits = pcr.cover(discharge_at_pits,0.0)*vos.secondsPerDay()
            #                       
            # TODO: At each segment, identify the maximum (average) discharge - in order to allow more abstraction
            #
            additional_storage += discharge_at_pits 
            self.readAvlChannelStorage += additional_storage

        # safety factor to reduce readAvlChannelStorage
        safety_factor = vos.getValDivZero(pcr.max(0.0, pcr.min(self.discharge, self.avgDischargeShort, self.avgDischarge)), minDischargeForEnvironmentalFlow, vos.smallNumber)
        safety_factor = pcr.max(0.00, safety_factor)
        safety_factor = pcr.min(1.00, safety_factor)
        self.readAvlChannelStorage = safety_factor * pcr.max(0.0, self.readAvlChannelStorage)                                                             

        # reduce readAvlChannelStorage with negative storage
        negative_storage = pcr.ifthen(self.channelStorage < 0.0, self.channelStorage) # unit: m3
        safety_factor_for_negative_storage = 2.5   
        self.readAvlChannelStorage = pcr.max(0.0, self.readAvlChannelStorage + pcr.cover(negative_storage*safety_factor_for_negative_storage, 0.0))

        # ignore small values - less than 1 m3
        self.readAvlChannelStorage = pcr.rounddown(self.readAvlChannelStorage)
        self.readAvlChannelStorage = pcr.ifthen(self.landmask, self.readAvlChannelStorage)

        if self.report == True:
            timeStamp = datetime.datetime(currTimeStep.year,\
                                          currTimeStep.month,\
                                          currTimeStep.day,\
                                          0)
            # writing daily output to netcdf files
            timestepPCR = currTimeStep.timeStepPCR
            if self.outDailyTotNC[0] != "None":
                for var in self.outDailyTotNC:
                    self.netcdfObj.data2NetCDF(str(self.outNCDir)+"/"+ \
                                         str(var)+"_dailyTot.nc",\
                                         var,\
                          pcr2numpy(self.__getattribute__(var),vos.MV),\
                                         timeStamp,timestepPCR-1)

            # writing monthly output to netcdf files
            # -cummulative
            if self.outMonthTotNC[0] != "None":
                for var in self.outMonthTotNC:

                    # introduce variables at the beginning of simulation or
                    #     reset variables at the beginning of the month
                    if currTimeStep.timeStepPCR == 1 or \
                       currTimeStep.day == 1:\
                       vars(self)[var+'MonthTot'] = pcr.scalar(0.0)

                    # accumulating
                    vars(self)[var+'MonthTot'] += vars(self)[var]

                    # reporting at the end of the month:
                    if currTimeStep.endMonth == True: 
                        self.netcdfObj.data2NetCDF(str(self.outNCDir)+"/"+ \
                                         str(var)+"_monthTot.nc",\
                                         var,\
                          pcr2numpy(self.__getattribute__(var+'MonthTot'),\
                           vos.MV),timeStamp,currTimeStep.monthIdx-1)
            # -average
            if self.outMonthAvgNC[0] != "None":
                for var in self.outMonthAvgNC:
                    # only if a accumulator variable has not been defined: 
                    if var not in self.outMonthTotNC: 

                        # introduce accumulator at the beginning of simulation or
                        #     reset accumulator at the beginning of the month
                        if currTimeStep.timeStepPCR == 1 or \
                           currTimeStep.day == 1:\
                           vars(self)[var+'MonthTot'] = pcr.scalar(0.0)
                        # accumulating
                        vars(self)[var+'MonthTot'] += vars(self)[var]

                    # calculating average & reporting at the end of the month:
                    if currTimeStep.endMonth == True:
                        vars(self)[var+'MonthAvg'] = vars(self)[var+'MonthTot']/\
                                                     currTimeStep.day  
                        self.netcdfObj.data2NetCDF(str(self.outNCDir)+"/"+ \
                                         str(var)+"_monthAvg.nc",\
                                         var,\
                          pcr2numpy(self.__getattribute__(var+'MonthAvg'),\
                           vos.MV),timeStamp,currTimeStep.monthIdx-1)
            #
            # -last day of the month
            if self.outMonthEndNC[0] != "None":
                for var in self.outMonthEndNC:
                    # reporting at the end of the month:
                    if currTimeStep.endMonth == True: 
                        self.netcdfObj.data2NetCDF(str(self.outNCDir)+"/"+ \
                                         str(var)+"_monthEnd.nc",\
                                         var,\
                          pcr2numpy(self.__getattribute__(var),vos.MV),\
                                         timeStamp,currTimeStep.monthIdx-1)

            # writing yearly output to netcdf files
            # -cummulative
            if self.outAnnuaTotNC[0] != "None":
                for var in self.outAnnuaTotNC:

                    # introduce variables at the beginning of simulation or
                    #     reset variables at the beginning of the month
                    if currTimeStep.timeStepPCR == 1 or \
                       currTimeStep.doy == 1:\
                       vars(self)[var+'AnnuaTot'] = pcr.scalar(0.0)

                    # accumulating
                    vars(self)[var+'AnnuaTot'] += vars(self)[var]

                    # reporting at the end of the year:
                    if currTimeStep.endYear == True: 
                        self.netcdfObj.data2NetCDF(str(self.outNCDir)+"/"+ \
                                         str(var)+"_annuaTot.nc",\
                                         var,\
                          pcr2numpy(self.__getattribute__(var+'AnnuaTot'),\
                           vos.MV),timeStamp,currTimeStep.annuaIdx-1)
            # -average
            if self.outAnnuaAvgNC[0] != "None":
                for var in self.outAnnuaAvgNC:
                    # only if a accumulator variable has not been defined: 
                    if var not in self.outAnnuaTotNC: 
                        # introduce accumulator at the beginning of simulation or
                        #     reset accumulator at the beginning of the year
                        if currTimeStep.timeStepPCR == 1 or \
                           currTimeStep.doy == 1:\
                           vars(self)[var+'AnnuaTot'] = pcr.scalar(0.0)
                        # accumulating
                        vars(self)[var+'AnnuaTot'] += vars(self)[var]
                    #
                    # calculating average & reporting at the end of the year:
                    if currTimeStep.endYear == True:
                        vars(self)[var+'AnnuaAvg'] = vars(self)[var+'AnnuaTot']/\
                                                     currTimeStep.doy  
                        self.netcdfObj.data2NetCDF(str(self.outNCDir)+"/"+ \
                                         str(var)+"_annuaAvg.nc",\
                                         var,\
                          pcr2numpy(self.__getattribute__(var+'AnnuaAvg'),\
                           vos.MV),timeStamp,currTimeStep.annuaIdx-1)
            #
            # -last day of the year
            if self.outAnnuaEndNC[0] != "None":
                for var in self.outAnnuaEndNC:
                    # reporting at the end of the year:
                    if currTimeStep.endYear == True: 
                        self.netcdfObj.data2NetCDF(str(self.outNCDir)+"/"+ \
                                         str(var)+"_annuaEnd.nc",\
                                         var,\
                          pcr2numpy(self.__getattribute__(var),vos.MV),\
                                         timeStamp,currTimeStep.annuaIdx-1)
