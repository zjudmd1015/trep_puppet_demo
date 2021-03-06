#!/usr/bin/env python
################
# ROS IMPORTS: #
################
import rospy
import tf
from skeletonmsgs_nu.msg import Skeletons
from skeletonmsgs_nu.msg import Skeleton
from skeletonmsgs_nu.msg import SkeletonJoint
from geometry_msgs.msg import PoseStamped as PS
import std_srvs.srv as SS
from geometry_msgs.msg import Pose as P
from geometry_msgs.msg import Point
from geometry_msgs.msg import Quaternion
import visualization_msgs.msg as VM

####################
# NON ROS IMPORTS: #
####################
import sys
import os
import math
import copy
from math import fmod, pi, copysign
import numpy as np
from frame_mappings import frame_map as fm

####################
# GLOBAL VARIABLES #
####################
DT = 1/100. # rate at which we will send out all of the control frames
SIMWF = 'world'
CONWF = 'camera_depth_optical_frame'
SCALING = 1
# filter parameters:
GAMMA = 0.05
A_LOW = 0.01
A_HIGH = 0.50
V_HIGH = 0.008
V_LOW = 0.001
# frequency divider for checking "key" skeleton:
FREQ_DIV = 30
ANG_MULT = 20
DIST_MULT = 1

def make_marker( color ):
    marker = VM.Marker()

    marker.type = VM.Marker.SPHERE
    marker.scale.x = 0.15
    marker.scale.y = 0.15
    marker.scale.z = 0.15
    marker.color.r = 0.1
    marker.color.g = 0.1
    marker.color.b = 0.1
    marker.color.a = 0.75
    if color == 'red':
        marker.color.r += 0.4
    elif color == 'blue':
        marker.color.b += 0.4
    elif color == 'green':
        marker.color.g += 0.4
    else:
        rospy.warn("Marker color not recognized!")

    return marker


class SingleController:
    def __init__(self, joint, conframe, simframe, simpos=(0.0,)*3, simquat=(0.0,)*4, color='green'):
        # Joint ~ user's  joint controlling the kinematic input
        # conframe ~ the frame that we should publish to control the kinematic
        #       input
        # simpos ~ nominal location of the kinematic config variable in trep
        #       simulation... used for determining offset
        # simquat ~ nominal orientation of the kinematic config var in the trep
        #       simulation... used for offset
        # simframe ~ the frame that the simpos and simquat point to
        # color ~ color of the marker attached to the frame
        self.joint = joint
        self.conframe = conframe
        self.simpos = simpos
        self.simquat = simquat
        self.simframe = simframe
        self.simpose_set = False
        self.first_flag = True
        # initialize the filter parameters:
        self.bn = np.zeros(3)
        self.prebn = np.zeros(3)
        self.prex = np.zeros(3)
        self.pos = simpos
        # init offset vec
        self.offset = np.zeros(3)
        self.act_pos = tuple((self.pos + self.offset).tolist())
        # make a marker
        self.marker = make_marker(color)
        self.marker.id = hash(conframe+simframe)%(2**16)
        self.marker.header.frame_id = SIMWF
        return


    def update_filter(self, pos):
        """
        Take in a skeleton message, and use self.joint to update the filter, and
        get an estimate of the pose of the joint
        """
        if not self.first_flag and self.simpose_set:
            # then run the filter
            vn = np.linalg.norm(pos - self.prex)
            if vn < V_LOW:
                falpha = A_LOW
            elif V_LOW <= vn <= V_HIGH:
                falpha = A_HIGH + ((vn-V_HIGH)/(V_LOW-V_HIGH))*\
                  (A_LOW-A_HIGH)
            elif vn > V_HIGH:
                falpha = A_HIGH
            else:
                falpha = (A_HIGH+A_LOW)/2.0
            pos = falpha*pos + (1-falpha)*(self.prex+self.bn)
            self.bn = GAMMA*(pos-self.prex) + (1-GAMMA)*self.prebn
            self.prebn = self.bn.copy()
            self.prex = pos.copy()
            self.pos = pos.copy()
            self.act_pos = tuple((self.pos + self.offset).tolist())
        else:
            # reset the filter
            self.bn = np.zeros(3)
            self.prebn = np.zeros(3)
            self.prex = np.array(pos)
            self.pos = np.array(pos)
            self.first_flag = False
            self.offset = np.array(self.simpos) - np.array(pos)
            self.act_pos = self.simpos
        self.update_marker_pose()
        return

    def update_marker_pose(self, pos=None):
        if pos == None:
            pos = self.act_pos
        self.marker.pose.position = Point(*pos)
        

    def update_simpose(self, pos=None, quat=None):
        if pos != None:
            self.simpos = pos
        if quat != None:
            self.simquat = quat
            self.quat = quat
        self.reset_all()
        self.simpose_set = True
        return


    def reset_all(self):
        self.bn = np.zeros(3)
        self.prebn = np.zeros(3)
        self.prex = np.zeros(3)
        self.pos = self.simpos
        # init offset vec
        self.offset = np.zeros(3)
        self.act_pos = tuple((self.pos + self.offset).tolist())
        self.simpose_set = False
        self.first_flag = True



class SkeletonController:
    def __init__(self):
        rospy.loginfo("Starting skeleton controller interface")
        self.legs_bool = rospy.get_param('legs', False)
        self.shoulders_bool = rospy.get_param('shoulders', False)
        # define tf broadcaster and listener:
        self.br = tf.TransformBroadcaster()
        self.listener = tf.TransformListener()
        # offer a service for resetting controls:
        self.reset_srv_provider = rospy.Service('interface_reset', SS.Empty,
                                                self.reset_provider)

        # create all of the controllers:
        self.running_flag = False
        self.controllers = []
        # hands
        for i in 4,5:
            self.controllers.append(SingleController(fm[i].user_frame,
                                                     fm[i].con_frame,
                                                     fm[i].input_frame,
                                                     color='green'))
        if self.shoulders_bool:
            for i in 2,3:
                self.controllers.append(SingleController(fm[i].user_frame,
                                                         fm[i].con_frame,
                                                         fm[i].input_frame,
                                                         color='red'))
        else:
            self.controllers.append(SingleController(fm[1].user_frame,
                                                     fm[1].con_frame,
                                                     fm[1].input_frame,
                                                     color='red'))
        if self.legs_bool:
            for i in 6,7:
                self.controllers.append(SingleController(fm[i].user_frame,
                                                         fm[i].con_frame,
                                                         fm[i].input_frame,
                                                         color='blue'))
        # Wait for the initial frames to be available, and store their poses:
        rospy.loginfo("Skeleton interface initially waiting for frames")
        while True:
            if self.wait_and_update_frames():
                self.running_flag = True
                break
            rospy.logwarn("Failure of waiting... trying again")
            rospy.sleep(50*DT)
        rospy.loginfo("Found all necessary frames")
        # let's get the value of the static transform between SIMFRAME and CONWF
        for i in range(10):
            try:
                pos,quat = self.listener.lookupTransform(SIMWF, CONWF, rospy.Time())
                self.g_sim_con = self.listener.fromTranslationRotation(pos, quat)
                # g_sim_con transforms from con frame to sim frame
                break
            except (tf.Exception):
                rospy.logwarn("Could not find transform between {0:s} and {1:s}!".
                              format(SIMWF, CONWF))
            rospy.sleep(0.5)

        # setup a timer to send out the key frames:
        rospy.Timer(rospy.Duration(DT), self.send_transforms)
        # define a subscriber to listen to the skeletons
        self.count = 0
        self.key_index = 0
        self.key_id = 1
        self.skel_sub = rospy.Subscriber("skeletons", Skeletons,
                                         self.skelcb)
        # define a publisher for the markers on the controls
        self.marker_pub = rospy.Publisher("visualization_markers", VM.MarkerArray)

        return


    def skelcb(self, data):
        if len(data.skeletons) == 0:
            return
        if self.count%FREQ_DIV == 0:
            self.get_key_user(data.skeletons)
        self.count += 1
        if self.key_index < len(data.skeletons) and \
                data.skeletons[self.key_index].userid == self.key_id:
            skel = data.skeletons[self.key_index]
        else:
            for i,skel in enumerate(data.skeletons):
                if skel.userid == self.key_id:
                    found = True
                    break
                found = False
            if not found:
                rospy.logwarn("Could not find a skeleton userid that matches the key user")
                return
        if self.running_flag:
            for i,con in enumerate(self.controllers):
                jtrans = skel.__getattribute__(con.joint).transform.translation
                # tracker mirrors, so flip x
                jpos = [-1.0*jtrans.x, jtrans.y, jtrans.z, 1]
                con.update_filter(np.dot(self.g_sim_con, jpos)[0:3])
        return


    def reset_provider(self, req):
        self.running_flag = False
        for i,con in enumerate(self.controllers):
            con.reset_all()
        # now tell all of the controllers to reset their filters:
        rospy.sleep(2.0)
        for i,con in enumerate(self.controllers):
            con.first_flag = True
            con.simpose_set = True
        self.running_flag = True
        return SS.EmptyResponse()


    def wait_and_update_frames(self):
        for i,con in enumerate(self.controllers):
            rospy.loginfo("Waiting for transform to "+con.simframe)
            for i in range(100):
                if self.listener.canTransform(SIMWF, con.simframe, rospy.Time()):
                    continue
                rospy.sleep(5*DT)
            rospy.loginfo("Found transform from {0:s} to {1:s}".format(
                    SIMWF, con.simframe))
            try:
                pos, quat = self.listener.lookupTransform(SIMWF, con.simframe, rospy.Time())
                con.update_simpose(pos, quat)
                rospy.loginfo(con.act_pos)
            except (tf.Exception):
                rospy.loginfo("Could not find transform from {0:s} to {1:s}".format(
                    SIMWF, con.simframe))
                return False
        return True


    def send_transforms(self, event):
        tnow = rospy.Time.now()
        mlist = []
        for i,con in enumerate(self.controllers):
            quat = con.quat
            if self.running_flag:
                pos = con.act_pos
                con.update_marker_pose(pos)
            else:
                pos = con.simpos
                con.update_marker_pose(pos)
            self.br.sendTransform(pos, quat,
                                  tnow, con.conframe, SIMWF)
            con.marker.header.stamp = tnow
            mlist.append(con.marker)
        ma = VM.MarkerArray()
        ma.markers = mlist
        self.marker_pub.publish(ma)
        return


    def get_key_user(self, skels):
        data = []
        for i,s in enumerate(skels):
            v2 = np.array([s.head.transform.translation.x,
                           s.head.transform.translation.z])
            ang = np.arccos(v2[1]/np.linalg.norm(v2))
            dist = v2[1]
            cost = ANG_MULT*ang + DIST_MULT*dist
            data.append([i, s.userid, cost])
        val, idx = min((val[2], idx) for (idx, val) in enumerate(data))
        self.key_index = data[idx][0]
        self.key_id = data[idx][1]
        return
    

def main():
    rospy.init_node('skeleton_interface')#, log_level=rospy.INFO)

    legs_bool = rospy.get_param('legs', False)
    if not rospy.has_param('legs'):
        rospy.set_param('legs', legs_bool)
    # same for the shoulders:
    shoulders_bool = rospy.get_param('shoulders', False)
    if not rospy.has_param('shoulders'):
        rospy.set_param('shoulders', shoulders_bool)

    try:
        sim = SkeletonController()
    except rospy.ROSInterruptException: pass

    rospy.spin()


if __name__=='__main__':
    main()
