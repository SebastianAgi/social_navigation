#!/usr/bin/env python3
from turtle import distance
import zed_interfaces.msg
import geometry_msgs.msg
import std_msgs.msg
import rospy
import time
import math
import os
import readchar
import sys
import message_filters
import numpy as np
from numpy import *
import spot_driver.msg

pub = rospy.Publisher('/obj_too_close', zed_interfaces.msg.FloatList, queue_size=10)
velo = zed_interfaces.msg.FloatList()
flag = False
resume_checkpoint = 1
past_time = 0
current_time = 0
current_checkpoint = 1
file_list = []
v_max = 1.6

path = '/home/prl-orin/ros_ws/src' #Directory to load Pose file from
load_file_name = "Pose_load.txt"
completeName_load = os.path.join(path, load_file_name)
pose_file = open(completeName_load, "r")
for line in pose_file:
        stripped_line = line.strip()
        line_list = stripped_line.split()
        file_list.append(line_list)

#Pre-load pose coordinates
Poses = []
for i in range(9):
    pose = geometry_msgs.msg.Pose()
    pose.position.x = float(file_list[3+i*12][1])
    pose.position.y = float(file_list[4+i*12][1])
    pose.position.z = float(file_list[5+i*12][1])
    pose.orientation.x = float(file_list[7+i*12][1])
    pose.orientation.y = float(file_list[8+i*12][1])
    pose.orientation.z = float(file_list[9+i*12][1])
    pose.orientation.w = float(file_list[10+i*12][1])
    Poses.append(pose)

def check_next_pose(x, y):
    global resume_checkpoint, Poses, current_checkpoint

    vec = np.array([[x - Poses[current_checkpoint].position.x],
                    [y - Poses[current_checkpoint].position.y]])

    if  np.linalg.norm(vec) < 1:
        current_checkpoint += 1
        if current_checkpoint == 4: # goal reached
            current_checkpoint = 3
            resume_checkpoint = 1

    return current_checkpoint, resume_checkpoint

def unit_vector(vector):
    """ Returns the unit vector of the vector.  """
    return vector / np.linalg.norm(vector)

def odom_to_baselink(spot, odom_dir):
    ang_rad = math.atan2(2 * spot.rotation.z * spot.rotation.w + 2 * spot.rotation.x * spot.rotation.y, 1 - 2 * spot.rotation.y**2 - 2 * spot.rotation.z**2)

    ang = ang_rad# - np.pi
    length = np.linalg.norm(np.array([[spot.translation.x],[spot.translation.y]]))

    baselink_desired_direction = np.array([[np.cos(ang)*odom_dir[0,0] + np.sin(ang)*odom_dir[1,0]],
                                           [-(np.sin(ang)*odom_dir[0,0]) + np.cos(ang)*odom_dir[1,0]]])

    # reqt = '\nang: {}\ndirection:\n{}\ndistance to point: {}'.format(ang*180/np.pi, baselink_desired_direction, round(length,3))
    # rospy.loginfo(reqt)

    return baselink_desired_direction

def desired_force(desired_direction, vmax, current_velo, RelaxationTime):
    force = np.add(desired_direction*v_max, -current_velo) / RelaxationTime
    
    # qt = '\ndd:{}  cv:{}   f:{}\n  {}    {}    {}\n'.format(round(desired_direction[0,0],3), round(current_velo[0,0],3), round(force[0,0],3),
    #                                                          round(desired_direction[1,0],3), round(current_velo[1,0],3), round(force[1,0],3))
    
    # rospy.loginfo(qt)
    return force

#spot is an np array (x,y vector), ped is obj_det_.objects
def social_force(spot, ped):
    forces = 0
    force = 0
    lambda_ = 0.75
    x = 0
    A = 0.1 # 10 #5.5
    B = -2.5 # -10.2 #-2
    C = 7 # 10 #1.7
    
    for i in range(len(ped.objects)):

        diff = np.array([[ped.objects[i].position[0]], 
                         [ped.objects[i].position[1]]])
        diffDirection = unit_vector(diff)
        diffDirection = np.reshape(diffDirection, (2,1))

        if ped.objects[i].action_state == 0:
            ped_velo = np.array([[0],
                                 [0]])
        else:
            ped_velo = np.array([[ped.objects[i].velocity[0]],
                                 [ped.objects[i].velocity[1]]])

        # velDiff = spot - ped_velo

        # interactionVector = np.add(lambda_ * velDiff, diff)

        x = np.linalg.norm(diff)
        
        repulsive_force = A * math.exp(B*x + C) # Ae^(Bx + C)
        
        force = np.array([[diffDirection[0,0]*repulsive_force],
                          [diffDirection[1,0]*repulsive_force]])
        
        forces += force
    
    return forces

def move(delta_time,socialforce, desiredforce, current_velo):

    a = np.add(-socialforce,desiredforce)

    if np.linalg.norm(current_velo) < 0.2:
        current_velo[0,0] = current_velo[1,0] = 0

    v = current_velo + delta_time * a
    
    roto = '\na: {}  cv: {}  v: {}\n   {}      {}     {}\n\ndelta_time: {}'.format(round(delta_time*a[0,0],3),round(current_velo[0,0],3),round(v[0,0],3),round(delta_time*a[1,0],3),round(current_velo[1,0],3),round(v[1,0],3), delta_time)
    rospy.loginfo(roto)

    return v

def duration(spot_pose,ped, cc):
    global Poses
    velocity = 1.6
    ped_dist = np.empty((0), float)

    for i in range(len(ped.objects)):
        temp = np.linalg.norm(np.array([[ped.objects[i].position[0]],[ped.objects[i].position[1]]]))
        temp_arr = np.array([temp])
        ped_dist = np.append(ped_dist, temp_arr, axis=0)

    if len(ped.objects) != 0:
        if ped_dist.min() > 3 and ped_dist.min() < 4:
            velocity = 1
        elif ped_dist.min() < 3:
            velocity = 0.7
        else:
            velocity = 1.7 
    
    distance_to_checkpoint = np.linalg.norm(np.array([[Poses[cc].position.x - spot_pose[0,0]],
                                                        [Poses[cc].position.y - spot_pose[1,0]]]))

    dur = distance_to_checkpoint/velocity

    return dur, velocity
        

def callback(spot, obj_det):
    global past_time, current_time, velo, flag, current_checkpoint, resume_checkpoint, Poses, v_max
    RelaxationTime = 0.2
    goal_x = Poses[current_checkpoint].position.x
    goal_y = Poses[current_checkpoint].position.y
    threshold = 0.1

    if flag == False:
        past_time = rospy.Time.now()
        flag = True

    spot_velo = np.array([[spot.velocity_of_body_in_vision.linear.x],
                           [spot.velocity_of_body_in_vision.linear.y]])

    spot_position = np.array([[spot.vision_tform_body.translation.x],
                              [spot.vision_tform_body.translation.y]])

    direction = np.array([[goal_x - spot_position[0,0]],
                          [goal_y - spot_position[1,0]]])

    odom_desired_direction = unit_vector(direction)

    desired_direction = odom_to_baselink(spot.vision_tform_body, odom_desired_direction)
    
    g_force = desired_force(desired_direction, 0.5, spot_velo, RelaxationTime)

    s_force = social_force(spot_velo, obj_det)
    
    current_checkpoint, resume_checkpoint = check_next_pose(spot_position[0,0],spot_position[1,0])

    if np.linalg.norm(s_force) > threshold:
        resume_checkpoint = 0
    else:
        resume_checkpoint = 1

    time, v_max = duration(spot_position, obj_det, current_checkpoint)

    current_time = rospy.Time.now()
    delta_time = float(rospy.Time.__str__(rospy.Time.__sub__(current_time,past_time)))/10**9
    
    new_spot_velo = move(delta_time,s_force, g_force, spot_velo)

    velo = [round(new_spot_velo[0,0],3), round(new_spot_velo[1,0],3), 0, current_checkpoint+1, time, v_max]

    # if len(obj_det.objects) != 0:
    #     forces = '\nrepulsive:  {}    attractive: {}          velo: {}\n           {}               {}               {}\n\nrepul norm: {},    attra norm: {},     velo norm: {}\nvelo: {}'.format(round(s_force[0,0],3),
    #                                                                                                                                                                                     round(g_force[0,0],3),
    #                                                                                                                                                                                     round(new_spot_velo[0,0],3),
    #                                                                                                                                                                                     round(s_force[1,0],3),
    #                                                                                                                                                                                     round(g_force[1,0],3),
    #                                                                                                                                                                                     round(new_spot_velo[1,0],3),
    #                                                                                                                                                                                     round(np.linalg.norm(s_force),3),
    #                                                                                                                                                                                     round(np.linalg.norm(g_force),3),
    #                                                                                                                                                                                     round(np.linalg.norm(new_spot_velo)),
    #                                                                                                                                                                                     velo)
    #     rospy.loginfo(forces)

    # strink = '\n{}\n{}'.format(np.linalg.norm(s_force),velo)
    # rospy.loginfo(velo)

    pub.publish(velo)
    
    past_time = current_time


def main():
    spot_kin = message_filters.Subscriber('/kinematic_state', spot_driver.msg.KinematicState)
    object_sub = message_filters.Subscriber('/zed2i/zed_node/obj_det/objects', zed_interfaces.msg.ObjectsStamped)

    ts = message_filters.ApproximateTimeSynchronizer([spot_kin, object_sub],queue_size = 100, slop = 0.1)
    ts.registerCallback(callback)

    rospy.spin()
    
if __name__ == '__main__':
    rospy.init_node("Subscriber_Node", anonymous=True)
    try:
        main()
    except rospy.ROSInterruptException:
        pass
